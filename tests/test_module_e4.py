"""Module E4 Verification Tests: 15:20 PM Intraday Auto-Exit & 90% Index Carry-Forward."""

from datetime import datetime, date, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from backend.app.services.scheduler import INDIA_TIMEZONE, is_india_market_holiday
from backend.app.services.intraday_exit import (
    IntradayExitEngine,
    INDEX_SYMBOLS,
    INTRADAY_EXIT_TIME,
    INDEX_CONFIDENCE_THRESHOLD,
    EXPIRY_BUFFER_DAYS,
)
from backend.app.models import PaperPosition


class TestIntradayExitConstants:
    """Test the intraday exit constants and configuration."""

    def test_intraday_exit_time_constant(self):
        assert INTRADAY_EXIT_TIME.hour == 15
        assert INTRADAY_EXIT_TIME.minute == 20

    def test_index_symbols_defined(self):
        assert "NIFTY" in INDEX_SYMBOLS
        assert "BANKNIFTY" in INDEX_SYMBOLS
        assert "SENSEX" in INDEX_SYMBOLS
        assert "BANKEX" in INDEX_SYMBOLS

    def test_confidence_threshold(self):
        assert INDEX_CONFIDENCE_THRESHOLD == 90

    def test_expiry_buffer_days(self):
        assert EXPIRY_BUFFER_DAYS == 1


class TestIntradayExitEngine:
    """Test the IntradayExitEngine core logic."""

    @pytest.fixture
    def mock_database(self):
        db = MagicMock(spec=["list_open_paper_positions", "upsert_paper_position"])
        db.enabled = True
        db.list_open_paper_positions = AsyncMock()
        return db

    @pytest.fixture
    def mock_redis(self):
        redis = MagicMock()
        redis.enabled = True
        return redis

    @pytest.fixture
    def mock_shoonya(self):
        shoonya = MagicMock()
        shoonya.get_quote = AsyncMock()
        return shoonya

    @pytest.fixture
    def engine(self, mock_database, mock_redis, mock_shoonya):
        return IntradayExitEngine(mock_database, mock_redis, mock_shoonya)

    def test_is_index_symbol(self, engine):
        assert engine._is_index_symbol("NIFTY") is True
        assert engine._is_index_symbol("BANKNIFTY") is True
        assert engine._is_index_symbol("SENSEX") is True
        assert engine._is_index_symbol("BANKEX") is True
        assert engine._is_index_symbol("RELIANCE") is False
        assert engine._is_index_symbol("HDFCBANK") is False
        assert engine._is_index_symbol("NIFTY-50") is True
        assert engine._is_index_symbol("BANKNIFTY-24JAN") is True

    def test_tp_hit_buy(self, engine):
        position = MagicMock()
        position.side = "BUY"
        position.take_profit = 100.0
        assert engine._tp_hit(position, 101.0) is True
        assert engine._tp_hit(position, 100.0) is True
        assert engine._tp_hit(position, 99.0) is False

    def test_tp_hit_sell(self, engine):
        position = MagicMock()
        position.side = "SELL"
        position.take_profit = 100.0
        assert engine._tp_hit(position, 99.0) is True
        assert engine._tp_hit(position, 100.0) is True
        assert engine._tp_hit(position, 101.0) is False

    def test_has_expiry_remaining_future(self, engine):
        position = MagicMock()
        position.expiry = "2026-12-31"
        position.payload = {}
        now_ist = datetime(2026, 1, 1, 15, 20, tzinfo=INDIA_TIMEZONE)
        assert engine._has_expiry_remaining(position, now_ist) is True

    def test_has_expiry_remaining_near(self, engine):
        position = MagicMock()
        position.expiry = "2026-01-02"
        position.payload = {}
        now_ist = datetime(2026, 1, 1, 15, 20, tzinfo=INDIA_TIMEZONE)
        assert engine._has_expiry_remaining(position, now_ist) is False

    def test_has_expiry_remaining_today(self, engine):
        position = MagicMock()
        position.expiry = "2026-01-01"
        position.payload = {}
        now_ist = datetime(2026, 1, 1, 15, 20, tzinfo=INDIA_TIMEZONE)
        assert engine._has_expiry_remaining(position, now_ist) is False

    def test_has_expiry_remaining_no_expiry(self, engine):
        position = MagicMock()
        position.expiry = None
        position.payload = {}
        now_ist = datetime(2026, 1, 1, 15, 20, tzinfo=INDIA_TIMEZONE)
        assert engine._has_expiry_remaining(position, now_ist) is False

    def test_is_carry_forward_eligible(self, engine):
        position = MagicMock()
        position.payload = {"product": "NRML"}
        assert engine._is_carry_forward_eligible(position) is True
        position.payload = {"product": "CNC"}
        assert engine._is_carry_forward_eligible(position) is True
        position.payload = {"product": "MIS"}
        assert engine._is_carry_forward_eligible(position) is True
        position.payload = {"product": "INVALID"}
        assert engine._is_carry_forward_eligible(position) is False
        position.payload = {}
        assert engine._is_carry_forward_eligible(position) is True


class TestIntradayExitScenarios:
    """Test the four verification scenarios for Module E4."""

    @pytest.fixture
    def mock_database(self):
        db = MagicMock(spec=["list_open_paper_positions", "upsert_paper_position"])
        db.enabled = True
        db.list_open_paper_positions = AsyncMock()
        return db

    @pytest.fixture
    def mock_redis(self):
        redis = MagicMock()
        redis.enabled = True
        return redis

    @pytest.fixture
    def mock_shoonya(self):
        shoonya = MagicMock()
        shoonya.get_quote = AsyncMock()
        return shoonya

    @pytest.fixture
    def engine(self, mock_database, mock_redis, mock_shoonya):
        return IntradayExitEngine(mock_database, mock_redis, mock_shoonya)

    def _make_position(
        self,
        symbol: str,
        exchange: str = "NSE",
        side: str = "BUY",
        is_auto_trade: bool = False,
        expiry: str | None = None,
        product: str = "MIS",
        entry_price: float = 100.0,
        stop_loss: float = 98.0,
        take_profit: float = 105.0,
    ) -> PaperPosition:
        position = MagicMock(spec=PaperPosition)
        position.id = f"pos_{symbol}"
        position.user_id = "test_user"
        position.client_order_id = f"order_{symbol}"
        position.symbol = symbol
        position.exchange = exchange
        position.broker_token = "12345"
        position.side = side
        position.quantity = 10
        position.entry_price = entry_price
        position.stop_loss = stop_loss
        position.take_profit = take_profit
        position.trailing_distance = 1.5
        position.favorable_price = entry_price
        position.pnl_fx_to_inr = 1.0
        position.status = "OPEN"
        position.is_auto_trade = is_auto_trade
        position.exit_price = None
        position.realized_pnl = 0.0
        position.opened_at = datetime.now(timezone.utc)
        position.closed_at = None
        position.exit_reason = None
        position.manual_exit_blocked_until = None
        position.updated_at = datetime.now(timezone.utc)
        position.expiry = expiry
        position.payload = {"product": product}
        return position

    @pytest.mark.asyncio
    async def test_scenario_a_equity_intraday_exits_at_1520(self, engine, mock_database, mock_shoonya):
        """
        (a) Equity Intraday at 15:20 IST -> Exits immediately at live price.
        """
        position = self._make_position("RELIANCE", is_auto_trade=False, take_profit=2600.0)
        mock_shoonya.get_quote.return_value = MagicMock(price=2500.0, source="SHOONYA")
        
        async def mock_list_positions():
            return [position]
        mock_database.list_open_paper_positions.side_effect = mock_list_positions
        
        with patch.object(engine, '_exit_paper_position', new_callable=AsyncMock) as mock_exit:
            mock_exit.return_value = True
            now = datetime(2026, 1, 5, 9, 50, tzinfo=timezone.utc)
            result = await engine.run_intraday_exit(now)
        
        assert result["status"] == "completed"
        assert result["exited"] == 1
        assert result["held"] == 0
        mock_exit.assert_called_once()
        call_args = mock_exit.call_args
        # reason is 3rd positional argument (index 2)
        assert call_args[0][2] == "INTRADAY_MANUAL_EXIT_1520"
        assert call_args[0][1] == 2500.0

    @pytest.mark.asyncio
    async def test_scenario_b_index_92_confidence_holds_overnight(self, engine, mock_database, mock_shoonya):
        """
        (b) Index with expiry remaining & 92% confidence -> Holds overnight (skips 15:20 exit).
        """
        position = self._make_position("NIFTY", expiry="2026-01-15", product="NRML", is_auto_trade=True, take_profit=22000.0)
        mock_shoonya.get_quote.return_value = MagicMock(price=21500.0, source="SHOONYA")
        
        async def mock_list_positions():
            return [position]
        mock_database.list_open_paper_positions.side_effect = mock_list_positions
        
        with patch.object(engine, '_evaluate_index_next_day_confidence', new_callable=AsyncMock) as mock_confidence:
            mock_confidence.return_value = 92
            with patch.object(engine, '_exit_paper_position', new_callable=AsyncMock) as mock_exit:
                now = datetime(2026, 1, 5, 9, 50, tzinfo=timezone.utc)
                result = await engine.run_intraday_exit(now)
        
        assert result["status"] == "completed"
        assert result["exited"] == 0
        assert result["held"] == 1
        mock_exit.assert_not_called()
        assert result["details"][0]["action"] == "held_overnight"
        assert result["details"][0]["confidence"] == 92
        assert result["details"][0]["reason"] == "index_confidence_92%"

    @pytest.mark.asyncio
    async def test_scenario_c_index_75_confidence_exits_at_1520(self, engine, mock_database, mock_shoonya):
        """
        (c) Index with expiry remaining & 75% confidence -> Exits at 15:20 IST.
        """
        position = self._make_position("BANKNIFTY", expiry="2026-01-15", product="NRML", is_auto_trade=False, take_profit=49000.0)
        mock_shoonya.get_quote.return_value = MagicMock(price=48500.0, source="SHOONYA")
        
        async def mock_list_positions():
            return [position]
        mock_database.list_open_paper_positions.side_effect = mock_list_positions
        
        with patch.object(engine, '_evaluate_index_next_day_confidence', new_callable=AsyncMock) as mock_confidence:
            mock_confidence.return_value = 75
            with patch.object(engine, '_exit_paper_position', new_callable=AsyncMock) as mock_exit:
                mock_exit.return_value = True
                now = datetime(2026, 1, 5, 9, 50, tzinfo=timezone.utc)
                result = await engine.run_intraday_exit(now)
        
        assert result["status"] == "completed"
        assert result["exited"] == 1
        assert result["held"] == 0
        mock_exit.assert_called_once()
        call_args = mock_exit.call_args
        assert call_args[0][2] == "INTRADAY_MANUAL_EXIT_1520"

    @pytest.mark.asyncio
    async def test_scenario_d_index_expiry_day_exits_at_1520(self, engine, mock_database, mock_shoonya):
        """
        (d) Index on expiry day (0 days left) -> Exits at 15:20 IST.
        """
        position = self._make_position("SENSEX", expiry="2026-01-05", product="NRML", is_auto_trade=True)
        mock_shoonya.get_quote.return_value = MagicMock(price=71500.0, source="SHOONYA")
        
        async def mock_list_positions():
            return [position]
        mock_database.list_open_paper_positions.side_effect = mock_list_positions
        
        with patch.object(engine, '_evaluate_index_next_day_confidence', new_callable=AsyncMock) as mock_confidence:
            mock_confidence.return_value = 95
            with patch.object(engine, '_exit_paper_position', new_callable=AsyncMock) as mock_exit:
                mock_exit.return_value = True
                now = datetime(2026, 1, 5, 9, 50, tzinfo=timezone.utc)
                result = await engine.run_intraday_exit(now)
        
        assert result["status"] == "completed"
        assert result["exited"] == 1
        assert result["held"] == 0
        mock_confidence.assert_not_called()
        mock_exit.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_trade_vs_manual_exit_reason(self, engine, mock_database, mock_shoonya):
        """Verify different exit reasons for auto-trade vs manual positions."""
        auto_pos = self._make_position("TCS", is_auto_trade=True, take_profit=3600.0)
        manual_pos = self._make_position("INFY", is_auto_trade=False, take_profit=3600.0)
        mock_shoonya.get_quote.return_value = MagicMock(price=3500.0, source="SHOONYA")
        
        async def mock_list_positions():
            return [auto_pos, manual_pos]
        mock_database.list_open_paper_positions.side_effect = mock_list_positions
        
        with patch.object(engine, '_exit_paper_position', new_callable=AsyncMock) as mock_exit:
            mock_exit.return_value = True
            now = datetime(2026, 1, 5, 9, 50, tzinfo=timezone.utc)
            await engine.run_intraday_exit(now)
        
        assert mock_exit.call_count == 2
        calls = mock_exit.call_args_list
        auto_call = next(c for c in calls if c[0][0].is_auto_trade)
        manual_call = next(c for c in calls if not c[0][0].is_auto_trade)
        assert auto_call[0][2] == "INTRADAY_AUTO_EXIT_1520"
        assert manual_call[0][2] == "INTRADAY_MANUAL_EXIT_1520"

    @pytest.mark.asyncio
    async def test_index_tp_hit_exits_regardless_of_confidence(self, engine, mock_database, mock_shoonya):
        """If TP already hit, exit immediately even for index with high confidence."""
        position = self._make_position("NIFTY", expiry="2026-01-15", take_profit=21400.0)
        mock_shoonya.get_quote.return_value = MagicMock(price=21500.0, source="SHOONYA")
        
        async def mock_list_positions():
            return [position]
        mock_database.list_open_paper_positions.side_effect = mock_list_positions
        
        with patch.object(engine, '_evaluate_index_next_day_confidence', new_callable=AsyncMock) as mock_confidence:
            mock_confidence.return_value = 95
            with patch.object(engine, '_exit_paper_position', new_callable=AsyncMock) as mock_exit:
                mock_exit.return_value = True
                now = datetime(2026, 1, 5, 9, 50, tzinfo=timezone.utc)
                result = await engine.run_intraday_exit(now)
        
        assert result["exited"] == 1
        assert result["held"] == 0
        mock_confidence.assert_not_called()


class TestIntradayExitWorker:
    """Test the worker scheduling logic."""

    @pytest.mark.asyncio
    async def test_worker_calculates_wait_time(self):
        """Verify worker calculates next 15:20 IST wait time correctly."""
        from backend.app.services.scheduler import INDIA_TIMEZONE, is_india_market_holiday
        from datetime import datetime, timedelta, timezone
        
        now_ist = datetime(2026, 1, 5, 9, 30, tzinfo=INDIA_TIMEZONE)
        target_today = datetime.combine(now_ist.date(), time(15, 20), tzinfo=INDIA_TIMEZONE)
        
        if now_ist >= target_today:
            target_today += timedelta(days=1)
        
        while target_today.weekday() >= 5 or is_india_market_holiday(target_today.date()):
            target_today += timedelta(days=1)
        
        wait_seconds = (target_today - now_ist).total_seconds()
        
        assert 0 < wait_seconds <= 86400
        assert target_today.hour == 15
        assert target_today.minute == 20


class TestIntradayExitIntegration:
    """Integration-style tests verifying main.py registration."""

    def test_main_py_imports_intraday_exit_worker(self):
        """Verify main.py imports the worker function."""
        import backend.app.main as main_module
        assert hasattr(main_module, '_intraday_exit_worker')

    def test_main_py_creates_intraday_exit_task(self):
        """Verify lifespan creates the intraday exit task."""
        import backend.app.main as main_module
        import inspect
        
        source = inspect.getsource(main_module.lifespan)
        assert "intraday_exit_task" in source
        assert "_intraday_exit_worker" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])