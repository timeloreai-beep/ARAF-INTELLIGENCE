from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache

import holidays

from ..config import get_settings


INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30))
SHOONYA_LOGIN_TIME = time(8, 45)


def _get_india_market_holidays(year: int) -> set[date]:
    """Get NSE/BSE holidays for a given year using the holidays library."""
    try:
        nse = holidays.NSE(years=[year])
        return {d for d in nse.keys()}
    except Exception:
        return set()


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
    # With the holidays library, any year is supported
    return True


def is_india_market_holiday(day: date) -> bool:
    configured = _configured_holidays()
    # Use dynamic NSE holidays library
    nse_holidays = _get_india_market_holidays(day.year)
    if nse_holidays:
        return day in nse_holidays or (configured is not None and day in configured)
    # Fallback to configured holidays if library fails
    if configured is not None:
        return day in configured
    return False


@lru_cache(maxsize=1)
def _get_holiday_cache() -> dict[int, list[str]]:
    result = {}
    # Generate cache for a range of years around current
    from datetime import datetime
    current_year = datetime.now().year
    for year in range(current_year - 2, current_year + 5):
        nse = holidays.NSE(years=[year])
        result[year] = sorted(d.isoformat() for d in nse.keys())
    return result


def get_known_holidays(year: int | None = None) -> dict[int, list[str]]:
    cache = _get_holiday_cache()
    if year is not None:
        return {year: cache.get(year, [])}
    return cache


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


def get_india_market_status(now: datetime | None = None) -> dict:
    current = (now or datetime.now(timezone.utc)).astimezone(INDIA_TIMEZONE)
    d = current.date()
    t = current.time().replace(tzinfo=None)

    is_holiday = is_india_market_holiday(d)
    is_weekend = current.weekday() >= 5

    if is_holiday:
        state = "CLOSED_HOLIDAY"
        reason = "Exchange Holiday"
    elif is_weekend:
        state = "CLOSED_WEEKEND"
        reason = "Weekend"
    elif t < time(9, 15):
        state = "PRE_OPEN"
        reason = "Pre-Market Session"
    elif time(9, 15) <= t <= time(15, 30):
        state = "OPEN_INTRADAY"
        reason = "Market Open"
    else:
        state = "CLOSED_SESSION"
        reason = "Post-Market Session"

    return {
        "is_open": state == "OPEN_INTRADAY",
        "state": state,
        "reason": reason,
        "exchange": "NSE/BSE",
        "current_time_ist": current.isoformat(),
        "next_open_ist": _next_market_open(current).isoformat() if not (is_holiday and is_weekend and state == "CLOSED_HOLIDAY") else None,
    }


def _next_market_open(current: datetime) -> datetime:
    next_day = current.date() + timedelta(days=1)
    while True:
        if next_day.weekday() < 5 and not is_india_market_holiday(next_day):
            return datetime.combine(next_day, time(9, 15), tzinfo=INDIA_TIMEZONE)
        next_day += timedelta(days=1)
