from datetime import datetime
from typing import Literal
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from .config import APEX_MAX_DAILY_SETUPS, DEFAULT_MAX_DAILY_TRADES


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


class OtpSendRequest(BaseModel):
    email: str = Field(validation_alias=AliasChoices("email", "to"), min_length=3, max_length=255)
    purpose: Literal["SIGNUP", "PIN_RESET"] = "SIGNUP"


class OtpVerifyRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    code: str = Field(pattern=r"^\d{6}$")
    purpose: Literal["SIGNUP", "PIN_RESET"] = "SIGNUP"


class TraderUidLoginRequest(BaseModel):
    trader_uid: str = Field(validation_alias=AliasChoices("trader_uid", "traderUid"), min_length=5, max_length=32)
    password: str = Field(min_length=6, max_length=256)


class KycSubmission(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    full_name: str = Field(validation_alias=AliasChoices("full_name", "fullName"), min_length=3, max_length=120)
    email: str | None = Field(default=None, min_length=3, max_length=180)
    pan: str = Field(pattern=r"^[A-Z]{5}\d{4}[A-Z]{1}$")
    aadhaar_last4: str = Field(validation_alias=AliasChoices("aadhaar_last4", "aadhaarLast4"), pattern=r"^\d{4}$")
    phone: str = Field(min_length=10, max_length=20)
    authorized_segment: Literal["ALL", "FNO_ONLY", "CRYPTO_ONLY"] = "ALL"
    dob: str | None = None
    income_bracket: str | None = Field(default=None, validation_alias=AliasChoices("income_bracket", "incomeBracket"), max_length=64)
    experience: str | None = Field(default=None, max_length=64)


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capital_inr: float = Field(gt=0)
    max_daily_loss_amount: float = Field(
        default=2500, ge=1, le=100_000_000, allow_inf_nan=False
    )
    max_trades_per_day: int = Field(default=DEFAULT_MAX_DAILY_TRADES, ge=1, le=APEX_MAX_DAILY_SETUPS)
    max_per_trade_risk_pct: float = Field(default=2.0, ge=0.1, le=2)


class RiskPnlSync(BaseModel):
    daily_pnl_inr: float = Field(allow_inf_nan=False)


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
    broker_token: str | None = Field(default=None, pattern=r"^\d{1,20}$")
    trailing_distance: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    client_order_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    is_auto_trade: bool = False
    automation_opportunity_id: str | None = Field(default=None, min_length=36, max_length=36)
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


class OptionSelectionRequest(BaseModel):
    underlying: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9.&_-]+$")
    direction: Literal["BULLISH", "BEARISH"]
    max_spread_pct: float = Field(default=2.0, gt=0, le=10, allow_inf_nan=False)


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


class EngineCandle(BaseModel):
    time: int | float | str
    open: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    low: float = Field(gt=0, allow_inf_nan=False)
    close: float = Field(gt=0, allow_inf_nan=False)
    volume: float = Field(default=0, ge=0, allow_inf_nan=False)
    buy_volume: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    sell_volume: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    is_closed: bool = False


class EngineUniverseItem(BaseModel):
    category: Literal["indian", "crypto", "commodity"]
    order_key: str = Field(min_length=1, max_length=64)
    change_pct: float = Field(allow_inf_nan=False)
    volume: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    turnover: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    sector: str | None = Field(default=None, max_length=80)


class EngineMacroSnapshot(BaseModel):
    usd_inr: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    vix: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    gold_change_pct: float | None = Field(default=None, allow_inf_nan=False)


class EngineL2Snapshot(BaseModel):
    has_l2: bool = False
    updated_at_ms: int | None = None
    best_bid: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    best_ask: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    buyers: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    sellers: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    depth_notional: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    source: str | None = Field(default=None, max_length=80)


class EngineEventRisk(BaseModel):
    state: str = "UNKNOWN"
    risk_level: str = "UNKNOWN"
    count: int = Field(default=0, ge=0)
    reason: str | None = Field(default=None, max_length=500)


class EngineNewsItem(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    trust_tier: str = "MEDIA"
    impact: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "NEUTRAL"


class EngineRegime(BaseModel):
    regime: str = "UNKNOWN"
    strategy_allowlist: list[str] = Field(default_factory=list, max_length=20)


class EngineCorrelation(BaseModel):
    status: str = "UNAVAILABLE"
    pearson: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)
    peer: str | None = Field(default=None, max_length=64)
    samples: int = Field(default=0, ge=0)


class EngineWalkForward(BaseModel):
    status: str = "INSUFFICIENT_DATA"
    trades: int = Field(default=0, ge=0)
    brier_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    overfitting_warning: bool = False
    profit_factor: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    net_return_pct: float | None = Field(default=None, allow_inf_nan=False)
    max_drawdown_pct: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class EngineQuantSnapshot(BaseModel):
    regime: EngineRegime | None = None
    correlation: EngineCorrelation | None = None
    walk_forward: EngineWalkForward | None = None


class EnginePosition(BaseModel):
    id: str | None = Field(default=None, max_length=100)
    symbol: str = Field(min_length=1, max_length=64)
    qty: float = Field(allow_inf_nan=False)


class EngineExecutionRoute(BaseModel):
    broker: Literal["SHOONYA", "COINDCX"]
    segment: str | None = Field(default=None, max_length=32)
    lot_size: float = Field(gt=0, allow_inf_nan=False)
    max_quantity: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    estimated_slippage_bps: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reject_rate_pct: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)


class EngineSizingBudget(BaseModel):
    daily_budget_inr: float = Field(gt=0, allow_inf_nan=False)
    used_budget_inr: float = Field(default=0, ge=0, allow_inf_nan=False)
    reserved_budget_inr: float = Field(default=0, ge=0, allow_inf_nan=False)
    capital_inr: float = Field(gt=0, allow_inf_nan=False)
    usd_inr: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class EngineMarketSession(BaseModel):
    is_open: bool | None = None
    state: str | None = Field(default=None, max_length=80)
    reason: str | None = Field(default=None, max_length=255)
    now_ist: datetime | None = None


class EngineScorecard(BaseModel):
    total_recorded: int = Field(default=0, ge=0)
    reject_rate_pct: float = Field(allow_inf_nan=False, ge=0, le=100)


class EngineEvaluationRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=64)
    order_key: str = Field(min_length=1, max_length=64)
    category: Literal["indian", "crypto", "commodity"] = "indian"
    timeframe: str = Field(default="15m", max_length=16)
    ltp: float = Field(default=0, ge=0, allow_inf_nan=False)
    sl: float = Field(default=0, ge=0, allow_inf_nan=False)
    target: float = Field(default=0, ge=0, allow_inf_nan=False)
    base_score: float = Field(default=0, ge=0, le=100, allow_inf_nan=False)
    base_side: Literal["BUY", "SELL", "NEUTRAL"] = "NEUTRAL"
    data_source: str = Field(default="NONE", max_length=100)
    data_quality: str = Field(default="OFFLINE", max_length=32)
    event_time_ms: int | None = None
    candles: list[EngineCandle] = Field(default_factory=list, max_length=500)
    universe: list[EngineUniverseItem] = Field(default_factory=list, max_length=5000)
    macro: EngineMacroSnapshot | None = None
    l2: EngineL2Snapshot | None = None
    event_risk: EngineEventRisk | None = None
    news_fetch_state: Literal["LOADED", "UNLOADED"] = "UNLOADED"
    news_items: list[EngineNewsItem] = Field(default_factory=list, max_length=500)
    quant: EngineQuantSnapshot | None = None
    positions: list[EnginePosition] | None = Field(default=None, max_length=500)
    execution: EngineExecutionRoute | None = None
    sizing: EngineSizingBudget | None = None
    session: EngineMarketSession | None = None
    scorecard: EngineScorecard | None = None


class GhostAdminLogin(BaseModel):
    password: str = Field(min_length=8, max_length=256)


class GhostAutoTradeControl(BaseModel):
    enabled: bool | None = None
    global_kill_switch: bool | None = None
    reason: str = Field(default="ADMIN_CONTROL", min_length=1, max_length=255)


class UserPreferencesPayload(BaseModel):
    theme: Literal["light", "dark"] | None = None
    news_lang: Literal["en-US", "bn-IN", "hi-IN", "gu-IN", "mr-IN", "ta-IN"] | None = None
    news_voice_digest_enabled: bool | None = None
    chart_timeframe: Literal["15s", "1m", "5m", "15m", "1h", "1d"] | None = None
