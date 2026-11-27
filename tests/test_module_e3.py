"""Module E3 Verification Tests: Shoonya Auto-Login, Smart Reconnect, CoinDCX Watchdog."""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.scheduler import (
    INDIA_TIMEZONE,
    seconds_until_shoonya_login,
    is_india_market_open,
    is_india_market_holiday,
    SHOONYA_LOGIN_TIME,
)
from backend.app.brokers.shoonya import (
    ShoonyaAdapter,
    ReconnectState,
)
from backend.app.brokers.coindcx import (
    CoinDCXAdapter,
    ConnectionState,
)


class TestShoonyaAutoLoginScheduler:
    """Test the 08:45 AM IST daily auto-login scheduler."""

    def test_shoonya_login_time_constant(self):
        assert SHOONYA_LOGIN_TIME.hour == 8
        assert SHOONYA_LOGIN_TIME.minute == 45

    def test_scheduler_targets_0845_ist_on_weekday(self):
        monday_0844 = datetime(2026, 1, 5, 8, 44, tzinfo=INDIA_TIMEZONE)
        wait = seconds_until_shoonya_login(monday_0844)
        assert wait == pytest.approx(60, abs=1)

    def test_scheduler_skips_weekend(self):
        friday_after = datetime(2026, 1, 9, 8, 46, tzinfo=INDIA_TIMEZONE)
        wait = seconds_until_shoonya_login(friday_after)
        next_run = friday_after + timedelta(seconds=wait)
        assert next_run.weekday() == 0  # Monday
        assert next_run.hour == 8
        assert next_run.minute == 45

    def test_scheduler_skips_market_holiday(self):
        holiday = datetime(2026, 1, 26, 8, 44, tzinfo=INDIA_TIMEZONE)
        assert is_india_market_holiday(holiday.date())
        wait = seconds_until_shoonya_login(holiday)
        next_run = holiday + timedelta(seconds=wait)
        assert next_run.date() == datetime(2026, 1, 27).date()
        assert next_run.hour == 8
        assert next_run.minute == 45


class TestTotpGeneration:
    """Test TOTP code generation for Shoonya login."""

    def test_totp_generation_with_valid_secret(self):
        adapter = ShoonyaAdapter()
        adapter.totp_secret = "JBSWY3DPEHPK3PXP"
        totp = adapter._totp()
        assert isinstance(totp, str)
        assert len(totp) == 6
        assert totp.isdigit()

    def test_totp_raises_without_secret(self):
        adapter = ShoonyaAdapter()
        adapter.totp_secret = ""
        with pytest.raises(RuntimeError, match="SHOONYA_TOTP_SECRET is required"):
            adapter._totp()


class TestShoonyaSmartReconnectStateMachine:
    """Test the conflict-aware smart reconnect state machine."""

    @pytest.fixture
    def mock_adapter(self):
        adapter = ShoonyaAdapter()
        adapter.user_id = "TEST_USER"
        adapter.api_key = "test_key"
        adapter.password = "test_pass"
        adapter.totp_secret = "JBSWY3DPEHPK3PXP"
        adapter.vendor_code = "TEST_U"
        return adapter

    def test_initial_state_is_idle(self, mock_adapter):
        assert mock_adapter.reconnect_state == ReconnectState.IDLE
        assert mock_adapter.reconnect_attempt == 0
        assert not mock_adapter.is_session_invalidated

    def test_conflict_detection_sets_state(self, mock_adapter):
        mock_adapter._set_conflict_detected()
        assert mock_adapter.reconnect_state == ReconnectState.CONFLICT_DETECTED
        assert mock_adapter.is_session_invalidated
        assert mock_adapter._last_conflict_time is not None

    def test_backoff_calculation_increases_exponentially(self, mock_adapter):
        mock_adapter._reconnect_attempt = 0
        backoff_0 = mock_adapter._calculate_backoff()
        assert 27 <= backoff_0 <= 33  # 30s ± 10%

        mock_adapter._reconnect_attempt = 1
        backoff_1 = mock_adapter._calculate_backoff()
        assert 54 <= backoff_1 <= 66  # 60s ± 10%

        mock_adapter._reconnect_attempt = 3
        backoff_3 = mock_adapter._calculate_backoff()
        assert 216 <= backoff_3 <= 264  # 240s ± 10%

    def test_max_backoff_capped(self, mock_adapter):
        mock_adapter._reconnect_attempt = 10
        backoff = mock_adapter._calculate_backoff()
        assert backoff <= 330  # Max 300s + 10% jitter

    def test_reset_clears_state(self, mock_adapter):
        mock_adapter._set_conflict_detected()
        mock_adapter._reconnect_attempt = 3
        mock_adapter._reset_reconnect_state()
        assert mock_adapter.reconnect_state == ReconnectState.IDLE
        assert mock_adapter.reconnect_attempt == 0
        assert not mock_adapter.is_session_invalidated

    @pytest.mark.asyncio
    async def test_smart_reconnect_backoff_flow(self, mock_adapter):
        mock_adapter._session_invalidated = True
        mock_adapter._reconnect_attempt = 0
        mock_adapter.MAX_RECONNECT_ATTEMPTS = 3

        async def mock_authenticate(token_key):
            if mock_authenticate.call_count == 0:
                mock_authenticate.call_count += 1
                raise RuntimeError("session invalid")
            elif mock_authenticate.call_count == 1:
                mock_authenticate.call_count += 1
                raise RuntimeError("session invalid")
            else:
                mock_adapter.session_token = "new_token"
                return {"stat": "Ok", "susertoken": "new_token"}

        mock_authenticate.call_count = 0

        with patch.object(mock_adapter, '_authenticate_and_cache', side_effect=mock_authenticate) as mock_auth:
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await mock_adapter.smart_reconnect()

                assert mock_auth.call_count == 3
                assert mock_sleep.call_count == 2
                assert mock_adapter.reconnect_attempt == 2
                assert mock_adapter.session_token == "new_token"


class TestCoinDCXWatchdog:
    """Test CoinDCX connectivity watchdog."""

    @pytest.fixture
    def mock_adapter(self):
        adapter = CoinDCXAdapter()
        adapter.api_key = "test_key"
        adapter.api_secret = "test_secret"
        return adapter

    def test_initial_connection_state(self, mock_adapter):
        assert mock_adapter.connection_state == ConnectionState.DISCONNECTED
        assert mock_adapter.reconnect_attempt == 0
        assert not mock_adapter.is_connected

    def test_backoff_calculation(self, mock_adapter):
        mock_adapter._reconnect_attempt = 0
        backoff_0 = mock_adapter._calculate_backoff()
        assert 4.5 <= backoff_0 <= 5.5

        mock_adapter._reconnect_attempt = 2
        backoff_2 = mock_adapter._calculate_backoff()
        assert 18 <= backoff_2 <= 22

    def test_max_backoff_capped(self, mock_adapter):
        mock_adapter._reconnect_attempt = 10
        backoff = mock_adapter._calculate_backoff()
        assert backoff <= 66  # Max 60s + 10% jitter

    def test_reset_clears_state(self, mock_adapter):
        mock_adapter._connection_state = ConnectionState.RECONNECTING
        mock_adapter._reconnect_attempt = 5
        mock_adapter._reset_reconnect_state()
        assert mock_adapter.connection_state == ConnectionState.CONNECTED
        assert mock_adapter.reconnect_attempt == 0

    @pytest.mark.asyncio
    async def test_watchdog_start_stop(self, mock_adapter):
        await mock_adapter.start_watchdog()
        assert mock_adapter._watchdog_running
        assert mock_adapter._watchdog_task is not None

        await mock_adapter.stop_watchdog()
        assert not mock_adapter._watchdog_running


class TestConflictDetectionTerms:
    """Test session conflict detection keywords."""

    def test_shoonya_conflict_terms_include_key_indicators(self):
        adapter = ShoonyaAdapter()
        terms = adapter.CONFLICT_DETECTION_TERMS
        assert "session" in terms
        assert "susertoken" in terms
        assert "jkey" in terms
        assert "login" in terms
        assert "invalid" in terms
        assert "expired" in terms
        assert "already logged" in terms
        assert "another session" in terms
        assert "force logout" in terms


if __name__ == "__main__":
    pytest.main([__file__, "-v"])