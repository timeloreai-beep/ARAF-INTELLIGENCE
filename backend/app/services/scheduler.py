from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from ..config import get_settings


INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30))
SHOONYA_LOGIN_TIME = time(8, 45)
NSE_BSE_HOLIDAYS = {
    2026: frozenset({
        date(2026, 1, 26),
        date(2026, 3, 3),
        date(2026, 3, 26),
        date(2026, 3, 31),
        date(2026, 4, 3),
        date(2026, 4, 14),
        date(2026, 5, 1),
        date(2026, 5, 28),
        date(2026, 6, 26),
        date(2026, 9, 14),
        date(2026, 10, 2),
        date(2026, 10, 20),
        date(2026, 11, 10),
        date(2026, 11, 24),
        date(2026, 12, 25),
    }),
}


def _configured_holidays() -> set[date] | None:
    configured = get_settings().india_market_holidays.strip()
    try:
        return {
            date.fromisoformat(value.strip())
            for value in configured.split(",")
            if value.strip()
        }
    except ValueError:
        return None


def _calendar_year_supported(year: int, configured: set[date] | None) -> bool:
    return year in NSE_BSE_HOLIDAYS or (
        configured is not None and any(day.year == year for day in configured)
    )


def is_india_market_holiday(day: date) -> bool:
    configured = _configured_holidays()
    if configured is None:
        return True
    baseline = NSE_BSE_HOLIDAYS.get(day.year)
    if baseline is not None:
        return day in baseline or day in configured
    if any(holiday.year == day.year for holiday in configured):
        return day in configured
    return True


def seconds_until_shoonya_login(now: datetime | None = None) -> float:
    current = (now or datetime.now(timezone.utc)).astimezone(INDIA_TIMEZONE)
    target = datetime.combine(current.date(), SHOONYA_LOGIN_TIME, tzinfo=INDIA_TIMEZONE)
    if current >= target:
        target += timedelta(days=1)
    configured = _configured_holidays()
    while target.weekday() >= 5 or is_india_market_holiday(target.date()):
        if not _calendar_year_supported(target.year, configured):
            next_check = datetime.combine(
                current.date() + timedelta(days=1),
                time(0, 5),
                tzinfo=INDIA_TIMEZONE,
            )
            return max(60.0, (next_check - current).total_seconds())
        target += timedelta(days=1)
    return max(0.0, (target - current).total_seconds())


def is_india_market_open(now: datetime | None = None) -> bool:
    current = (now or datetime.now(timezone.utc)).astimezone(INDIA_TIMEZONE)
    return (
        current.weekday() < 5
        and not is_india_market_holiday(current.date())
        and time(9, 15) <= current.time().replace(tzinfo=None) <= time(15, 30)
    )
