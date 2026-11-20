from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    environment: str
    live_trading_enabled: bool


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=6, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PinRequest(BaseModel):
    pin: str = Field(pattern=r"^\d{6}$")


class KycSubmission(BaseModel):
    full_name: str = Field(min_length=3, max_length=120)
    email: str = Field(min_length=3, max_length=180)
    pan: str = Field(pattern=r"^[A-Z]{5}\d{4}[A-Z]{1}$")
    aadhaar_last4: str = Field(pattern=r"^\d{4}$")
    phone: str = Field(min_length=10, max_length=20)
    authorized_segment: Literal["ALL", "FNO_ONLY", "CRYPTO_ONLY"] = "ALL"


class RiskConfig(BaseModel):
    capital_inr: float = Field(gt=0)
    max_daily_loss_pct: float = Field(default=2.5, ge=0.5, le=10)
    max_trades_per_day: int = Field(default=5, ge=1, le=20)
    max_per_trade_risk_pct: float = Field(default=2.0, ge=0.1, le=2)


class OrderRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=30)
    exchange: Literal["NSE", "BSE", "NFO", "BFO", "COINDCX"] = "NSE"
    side: Literal["BUY", "SELL"]
    quantity: float = Field(gt=0)
    operation: Literal["ENTRY", "EXIT"] = "ENTRY"
    mode: Literal["PAPER", "LIVE"]
    stop_loss: float | None = None
    take_profit: float | None = None
    entry_price: float | None = Field(default=None, gt=0)
    client_order_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    macro_green: bool = False
    smc_green: bool = False
    mtf_green: bool = False
    volume_green: bool = False


class GreeksRequest(BaseModel):
    spot: float = Field(gt=0)
    strike: float = Field(gt=0)
    annualized_rate: float
    volatility: float = Field(gt=0)
    time_to_expiry_years: float = Field(gt=0)
    dividend_yield: float = Field(ge=0, default=0)


class GreeksResponse(BaseModel):
    price: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float


class ScannerRequest(BaseModel):
    exchange: Literal["ALL", "NSE", "BSE", "CRYPTO"] = "ALL"
    eligible_only: bool = True
    limit: int = Field(default=100, ge=1, le=5000)
