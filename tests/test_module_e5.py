"""Module E5 Verification Tests: Automatic Trailing Stop-Loss (TSL) Engine."""

from datetime import datetime, timezone
import pytest

from backend.app.services.paper_lifecycle import advance_paper_position


class TestTrailingStopLossEngine:
    """Test the TSL engine core logic for both LONG and SHORT positions."""

    def _make_long_position(
        self,
        entry_price: float = 100.0,
        stop_loss: float = 90.0,
        take_profit: float = 120.0,
        trailing_distance: float = 5.0,
        favorable_price: float | None = None,
    ) -> dict:
        return {
            "id": "pos_long_1",
            "user_id": "test_user",
            "client_order_id": "order_long_1",
            "symbol": "NIFTY",
            "exchange": "NFO",
            "broker_token": "26000",
            "side": "BUY",
            "quantity": 75.0,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "trailing_distance": trailing_distance,
            "favorable_price": favorable_price if favorable_price is not None else entry_price,
            "status": "OPEN",
            "is_auto_trade": False,
            "exit_price": None,
            "realized_pnl": 0.0,
            "opened_at": datetime.now(timezone.utc),
            "closed_at": None,
            "exit_reason": None,
            "manual_exit_blocked_until": None,
            "pnl_fx_to_inr": 1.0,
        }

    def _make_short_position(
        self,
        entry_price: float = 100.0,
        stop_loss: float = 110.0,
        take_profit: float = 80.0,
        trailing_distance: float = 5.0,
        favorable_price: float | None = None,
    ) -> dict:
        return {
            "id": "pos_short_1",
            "user_id": "test_user",
            "client_order_id": "order_short_1",
            "symbol": "NIFTY",
            "exchange": "NFO",
            "broker_token": "26000",
            "side": "SELL",
            "quantity": 75.0,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "trailing_distance": trailing_distance,
            "favorable_price": favorable_price if favorable_price is not None else entry_price,
            "status": "OPEN",
            "is_auto_trade": False,
            "exit_price": None,
            "realized_pnl": 0.0,
            "opened_at": datetime.now(timezone.utc),
            "closed_at": None,
            "exit_reason": None,
            "manual_exit_blocked_until": None,
            "pnl_fx_to_inr": 1.0,
        }

    # ==================== LONG (BUY) Position Tests ====================

    def test_long_initial_state(self):
        """LONG position starts with initial SL and favorable price = entry.
        
        Note: On first tick at entry price, SL immediately trails to entry - trailing_distance.
        """
        pos = self._make_long_position()
        updated = advance_paper_position(pos, market_price=100.0)
        
        assert updated["status"] == "OPEN"
        # On first tick at entry price (100), favorable = max(100, 100) = 100
        # SL = max(90, 100 - 5) = max(90, 95) = 95
        assert updated["stop_loss"] == 95.0
        assert updated["favorable_price"] == 100.0

    def test_long_price_rises_sl_trails_upward(self):
        """
        (a) LONG entry 100, SL 90, TP 120 -> price rises to 112
        -> SL trails upward automatically (e.g. > 100)
        """
        pos = self._make_long_position(entry_price=100.0, stop_loss=90.0, take_profit=120.0, trailing_distance=5.0)
        
        # Price rises to 112
        updated = advance_paper_position(pos, market_price=112.0)
        
        # Favorable price should be 112 (highest reached)
        assert updated["favorable_price"] == 112.0
        # SL should trail: favorable - trailing_distance = 112 - 5 = 107
        # But never below original SL of 90, so max(90, 107) = 107
        assert updated["stop_loss"] == 107.0
        # SL is now ABOVE entry price (100), locking in profit
        assert updated["stop_loss"] > 100.0

    def test_long_price_drops_sl_stays_locked(self):
        """
        (a continued) Price drops slightly to 110 -> SL stays locked and NEVER moves back down!
        """
        pos = self._make_long_position(entry_price=100.0, stop_loss=90.0, take_profit=120.0, trailing_distance=5.0)
        
        # First, price rises to 112, SL trails to 107
        updated = advance_paper_position(pos, market_price=112.0)
        assert updated["stop_loss"] == 107.0
        assert updated["favorable_price"] == 112.0
        
        # Then price drops to 110
        updated2 = advance_paper_position(updated, market_price=110.0)
        
        # Favorable price should stay at 112 (highest ever reached)
        assert updated2["favorable_price"] == 112.0
        # SL should stay at 107 (NEVER moves back down)
        assert updated2["stop_loss"] == 107.0

    def test_long_multiple_price_moves_sl_only_moves_up(self):
        """SL only moves up, never down, through multiple price fluctuations."""
        pos = self._make_long_position(entry_price=100.0, stop_loss=90.0, take_profit=120.0, trailing_distance=5.0)
        
        # Price goes: 100 -> 105 -> 103 -> 108 -> 106 -> 115
        updated = advance_paper_position(pos, market_price=105.0)
        assert updated["stop_loss"] == 100.0  # 105 - 5 = 100
        assert updated["favorable_price"] == 105.0
        
        updated = advance_paper_position(updated, market_price=103.0)
        assert updated["stop_loss"] == 100.0  # Stays at 100
        assert updated["favorable_price"] == 105.0  # Stays at 105
        
        updated = advance_paper_position(updated, market_price=108.0)
        assert updated["stop_loss"] == 103.0  # 108 - 5 = 103
        assert updated["favorable_price"] == 108.0
        
        updated = advance_paper_position(updated, market_price=106.0)
        assert updated["stop_loss"] == 103.0  # Stays at 103
        assert updated["favorable_price"] == 108.0  # Stays at 108
        
        updated = advance_paper_position(updated, market_price=115.0)
        assert updated["stop_loss"] == 110.0  # 115 - 5 = 110
        assert updated["favorable_price"] == 115.0

    def test_long_tp_hit_closes_position(self):
        """LONG position closes when take profit is hit."""
        pos = self._make_long_position(entry_price=100.0, stop_loss=90.0, take_profit=120.0, trailing_distance=5.0)
        
        updated = advance_paper_position(pos, market_price=120.0)
        
        assert updated["status"] == "CLOSED"
        assert updated["exit_price"] == 120.0
        assert updated["exit_reason"] is None or "target" in str(updated.get("exit_reason", "")).lower()
        assert updated["realized_pnl"] > 0

    def test_long_sl_hit_closes_position(self):
        """LONG position closes when stop loss is hit."""
        pos = self._make_long_position(entry_price=100.0, stop_loss=90.0, take_profit=120.0, trailing_distance=5.0)
        
        updated = advance_paper_position(pos, market_price=90.0)
        
        assert updated["status"] == "CLOSED"
        assert updated["exit_price"] == 90.0
        assert updated["realized_pnl"] < 0

    def test_long_sl_trails_above_entry_locks_profit(self):
        """Verify SL trails above entry price, locking in profit."""
        pos = self._make_long_position(entry_price=100.0, stop_loss=90.0, take_profit=120.0, trailing_distance=5.0)
        
        # Price rises to 108
        updated = advance_paper_position(pos, market_price=108.0)
        
        # SL = 108 - 5 = 103, which is > entry (100)
        assert updated["stop_loss"] == 103.0
        assert updated["stop_loss"] > pos["entry_price"]

    # ==================== SHORT (SELL) Position Tests ====================

    def test_short_initial_state(self):
        """SHORT position starts with initial SL and favorable price = entry.
        
        Note: On first tick at entry price, SL immediately trails to entry + trailing_distance.
        """
        pos = self._make_short_position()
        updated = advance_paper_position(pos, market_price=100.0)
        
        assert updated["status"] == "OPEN"
        # On first tick at entry price (100), favorable = min(100, 100) = 100
        # SL = min(110, 100 + 5) = min(110, 105) = 105
        assert updated["stop_loss"] == 105.0
        assert updated["favorable_price"] == 100.0

    def test_short_price_drops_sl_trails_downward(self):
        """
        (b) SHORT entry 100, SL 110, TP 80 -> price drops to 88
        -> SL trails downward automatically (e.g. < 100)
        """
        pos = self._make_short_position(entry_price=100.0, stop_loss=110.0, take_profit=80.0, trailing_distance=5.0)
        
        # Price drops to 88
        updated = advance_paper_position(pos, market_price=88.0)
        
        # Favorable price should be 88 (lowest reached)
        assert updated["favorable_price"] == 88.0
        # SL should trail: favorable + trailing_distance = 88 + 5 = 93
        # But never above original SL of 110, so min(110, 93) = 93
        assert updated["stop_loss"] == 93.0
        # SL is now BELOW entry price (100), locking in profit
        assert updated["stop_loss"] < 100.0

    def test_short_price_bounces_sl_stays_locked(self):
        """
        (b continued) Price bounces to 90 -> SL stays locked and NEVER moves back up!
        """
        pos = self._make_short_position(entry_price=100.0, stop_loss=110.0, take_profit=80.0, trailing_distance=5.0)
        
        # First, price drops to 88, SL trails to 93
        updated = advance_paper_position(pos, market_price=88.0)
        assert updated["stop_loss"] == 93.0
        assert updated["favorable_price"] == 88.0
        
        # Then price bounces to 90
        updated2 = advance_paper_position(updated, market_price=90.0)
        
        # Favorable price should stay at 88 (lowest ever reached)
        assert updated2["favorable_price"] == 88.0
        # SL should stay at 93 (NEVER moves back up)
        assert updated2["stop_loss"] == 93.0

    def test_short_multiple_price_moves_sl_only_moves_down(self):
        """SL only moves down, never up, through multiple price fluctuations."""
        pos = self._make_short_position(entry_price=100.0, stop_loss=110.0, take_profit=80.0, trailing_distance=5.0)
        
        # Price goes: 100 -> 95 -> 97 -> 92 -> 94 -> 85
        updated = advance_paper_position(pos, market_price=95.0)
        assert updated["stop_loss"] == 100.0  # 95 + 5 = 100
        assert updated["favorable_price"] == 95.0
        
        updated = advance_paper_position(updated, market_price=97.0)
        assert updated["stop_loss"] == 100.0  # Stays at 100
        assert updated["favorable_price"] == 95.0  # Stays at 95
        
        updated = advance_paper_position(updated, market_price=92.0)
        assert updated["stop_loss"] == 97.0  # 92 + 5 = 97
        assert updated["favorable_price"] == 92.0
        
        updated = advance_paper_position(updated, market_price=94.0)
        assert updated["stop_loss"] == 97.0  # Stays at 97
        assert updated["favorable_price"] == 92.0  # Stays at 92
        
        updated = advance_paper_position(updated, market_price=85.0)
        assert updated["stop_loss"] == 90.0  # 85 + 5 = 90
        assert updated["favorable_price"] == 85.0

    def test_short_tp_hit_closes_position(self):
        """SHORT position closes when take profit is hit."""
        pos = self._make_short_position(entry_price=100.0, stop_loss=110.0, take_profit=80.0, trailing_distance=5.0)
        
        updated = advance_paper_position(pos, market_price=80.0)
        
        assert updated["status"] == "CLOSED"
        assert updated["exit_price"] == 80.0
        assert updated["realized_pnl"] > 0

    def test_short_sl_hit_closes_position(self):
        """SHORT position closes when stop loss is hit."""
        pos = self._make_short_position(entry_price=100.0, stop_loss=110.0, take_profit=80.0, trailing_distance=5.0)
        
        updated = advance_paper_position(pos, market_price=110.0)
        
        assert updated["status"] == "CLOSED"
        assert updated["exit_price"] == 110.0
        assert updated["realized_pnl"] < 0

    def test_short_sl_trails_below_entry_locks_profit(self):
        """Verify SL trails below entry price, locking in profit."""
        pos = self._make_short_position(entry_price=100.0, stop_loss=110.0, take_profit=80.0, trailing_distance=5.0)
        
        # Price drops to 92
        updated = advance_paper_position(pos, market_price=92.0)
        
        # SL = 92 + 5 = 97, which is < entry (100)
        assert updated["stop_loss"] == 97.0
        assert updated["stop_loss"] < pos["entry_price"]

    # ==================== Edge Cases ====================

    def test_closed_position_not_updated(self):
        """Closed positions should not be updated."""
        pos = self._make_long_position()
        pos["status"] = "CLOSED"
        
        updated = advance_paper_position(pos, market_price=150.0)
        
        assert updated["status"] == "CLOSED"
        assert updated == pos  # Should return copy unchanged

    def test_invalid_values_raises_error(self):
        """Invalid values should raise ValueError."""
        pos = self._make_long_position()
        pos["entry_price"] = -100.0
        
        with pytest.raises(ValueError, match="invalid market or risk values"):
            advance_paper_position(pos, market_price=100.0)

    def test_trailing_distance_small(self):
        """Very small trailing distance should work (SL close to favorable price)."""
        pos = self._make_long_position(entry_price=100.0, stop_loss=90.0, take_profit=120.0, trailing_distance=0.01)
        
        updated = advance_paper_position(pos, market_price=110.0)
        
        assert updated["stop_loss"] == 109.99  # favorable - 0.01 = 109.99
        assert updated["favorable_price"] == 110.0

    def test_long_price_never_exceeds_tp_before_close(self):
        """Price above TP should close position."""
        pos = self._make_long_position(entry_price=100.0, stop_loss=90.0, take_profit=120.0, trailing_distance=5.0)
        
        updated = advance_paper_position(pos, market_price=125.0)
        
        assert updated["status"] == "CLOSED"
        assert updated["exit_price"] == 125.0

    def test_short_price_never_below_tp_before_close(self):
        """Price below TP should close position."""
        pos = self._make_short_position(entry_price=100.0, stop_loss=110.0, take_profit=80.0, trailing_distance=5.0)
        
        updated = advance_paper_position(pos, market_price=75.0)
        
        assert updated["status"] == "CLOSED"
        assert updated["exit_price"] == 75.0


class TestTrailingStopLossIntegration:
    """Integration-style tests verifying the TSL works in the paper position worker context."""

    def test_paper_position_worker_uses_advance_paper_position(self):
        """Verify main.py's _paper_position_worker calls advance_paper_position."""
        import backend.app.main as main_module
        import inspect
        
        source = inspect.getsource(main_module._paper_position_worker)
        assert "advance_paper_position" in source
        # The import is at module level
        module_source = inspect.getsource(main_module)
        assert "from .services.paper_lifecycle import advance_paper_position" in module_source

    def test_trailing_fields_persisted_in_database(self):
        """Verify PaperPosition model has trailing_distance and favorable_price fields."""
        from backend.app.models import PaperPosition
        from sqlalchemy import inspect as sa_inspect
        
        mapper = sa_inspect(PaperPosition)
        columns = {c.key for c in mapper.columns}
        
        assert "trailing_distance" in columns
        assert "favorable_price" in columns
        assert "stop_loss" in columns

    def test_automation_api_returns_trailing_fields(self):
        """Verify the automation positions API returns trailing fields."""
        import backend.app.routers.automation as automation_module
        import inspect
        
        source = inspect.getsource(automation_module.get_paper_positions)
        assert "trailingDistance" in source
        assert "favorablePrice" in source
        assert "trailing_distance" in source
        assert "favorable_price" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])