from fastapi import APIRouter

from ..quant.greeks import black_scholes
from ..schemas import GreeksRequest, GreeksResponse

router = APIRouter(prefix="/options", tags=["options"])


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
