import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from redis.exceptions import RedisError

from ..brokers.shoonya import ShoonyaAdapter
from ..dependencies import current_subject
from ..quant.greeks import black_scholes
from ..schemas import GreeksRequest, GreeksResponse, OptionSelectionRequest
from ..services.options import select_liquid_shoonya_option

router = APIRouter(prefix="/options", tags=["options"])


@router.post("/select")
async def select_option_contract(
    payload: OptionSelectionRequest,
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    del subject
    adapter = ShoonyaAdapter(redis=getattr(request.app.state, "redis", None))
    try:
        await adapter.login()
        selection = await select_liquid_shoonya_option(
            adapter,
            underlying=payload.underlying,
            direction=payload.direction,
            max_spread_pct=payload.max_spread_pct,
        )
    except (RuntimeError, ValueError, httpx.HTTPError, RedisError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "SHOONYA_OPTION_SELECTION_UNAVAILABLE"},
        ) from exc
    return {"success": True, "selection": selection, "read_only": True}


@router.post("/greeks", response_model=GreeksResponse)
async def calculate_greeks(payload: GreeksRequest) -> GreeksResponse:
    result = black_scholes(
        spot=payload.spot,
        strike=payload.strike,
        annualized_rate=payload.annualized_rate,
        volatility=payload.volatility,
        time_to_expiry_years=payload.time_to_expiry_years,
        dividend_yield=payload.dividend_yield,
        option_type="call",
    )
    return GreeksResponse(**result.__dict__)


@router.post("/greeks/put", response_model=GreeksResponse)
async def calculate_put_greeks(payload: GreeksRequest) -> GreeksResponse:
    result = black_scholes(
        spot=payload.spot,
        strike=payload.strike,
        annualized_rate=payload.annualized_rate,
        volatility=payload.volatility,
        time_to_expiry_years=payload.time_to_expiry_years,
        dividend_yield=payload.dividend_yield,
        option_type="put",
    )
    return GreeksResponse(**result.__dict__)
