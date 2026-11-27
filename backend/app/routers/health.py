from fastapi import APIRouter
from datetime import datetime, timezone

from ..config import get_settings
from ..schemas import HealthResponse
from ..services.scheduler import (
    is_india_market_open,
    get_india_market_status,
    get_known_holidays,
    INDIA_TIMEZONE,
)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        environment=settings.app_env,
        live_trading_enabled=settings.live_trading_enabled,
    )


@router.get("/market/status")
async def market_status() -> dict:
    return get_india_market_status()


@router.get("/market/holidays")
async def market_holidays(year: int | None = None) -> dict:
    return get_known_holidays(year)
