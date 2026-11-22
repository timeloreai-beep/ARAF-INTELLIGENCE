from datetime import datetime, timedelta, timezone

from backend.app.services.scheduler import (
    INDIA_TIMEZONE,
    is_india_market_open,
    is_india_market_holiday,
    seconds_until_shoonya_login,
)


def test_shoonya_login_schedule_targets_0845_ist_and_skips_weekends():
    before_open = datetime(2026, 1, 5, 8, 44, tzinfo=INDIA_TIMEZONE)
    assert seconds_until_shoonya_login(before_open) == 60

    friday_after_open = datetime(2026, 1, 9, 8, 46, tzinfo=INDIA_TIMEZONE)
    next_run = friday_after_open + timedelta(
        seconds=seconds_until_shoonya_login(friday_after_open)
    )
    assert next_run == datetime(2026, 1, 12, 8, 45, tzinfo=INDIA_TIMEZONE)


def test_india_session_window_is_weekday_only_and_includes_close():
    assert is_india_market_open(datetime(2026, 1, 5, 9, 15, tzinfo=INDIA_TIMEZONE))
    assert is_india_market_open(datetime(2026, 1, 5, 15, 30, tzinfo=INDIA_TIMEZONE))
    assert not is_india_market_open(datetime(2026, 1, 5, 9, 14, tzinfo=INDIA_TIMEZONE))
    assert not is_india_market_open(datetime(2026, 1, 10, 10, 0, tzinfo=INDIA_TIMEZONE))


def test_official_market_holidays_skip_login_and_scanning():
    holiday = datetime(2026, 1, 26, 8, 44, tzinfo=INDIA_TIMEZONE)

    assert is_india_market_holiday(holiday.date())
    assert not is_india_market_open(datetime(2026, 1, 26, 10, 0, tzinfo=INDIA_TIMEZONE))
    assert seconds_until_shoonya_login(holiday) == (
        datetime(2026, 1, 27, 8, 45, tzinfo=INDIA_TIMEZONE) - holiday
    ).total_seconds()
