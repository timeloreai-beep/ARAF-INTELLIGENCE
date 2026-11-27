from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


APEX_MAX_DAILY_SETUPS = 1000
DEFAULT_MAX_DAILY_TRADES = 3


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_secret: str = ""
    jwt_secret: str = ""
    access_token_ttl_minutes: int = 30
    pin_ttl_minutes: int = 720
    cors_origins: str = "http://localhost:3000"
    live_trading_enabled: bool = False
    legacy_worker_url: str = "https://soft-dawn-28a0.timeloreai.workers.dev/"

    supabase_database_url: str = ""
    neon_database_url: str = ""
    mongodb_atlas_url: str = ""
    mongodb_database: str = "araf"
    upstash_redis_url: str = ""
    firebase_project_id: str = ""
    firebase_service_account_json: str = ""
    firebase_web_api_key: str = ""
    gmail_bridge_url: str = ""
    gmail_bridge_secret: str = ""
    kyc_pii_encryption_key: str = ""

    shoonya_relay_url: str = ""
    shoonya_api_key: str = ""
    shoonya_user_id: str = ""
    shoonya_password: str = ""
    shoonya_totp_secret: str = ""
    shoonya_vendor_code: str = ""

    coindcx_api_key: str = ""
    coindcx_api_secret: str = ""
    coindcx_relay_url: str = ""

    news_provider_url: str = ""
    gemini_api_key: str = ""
    admin_password: str = ""
    admin_upi_id: str = ""
    admin_name: str = ""
    telegram_bot_token: str = ""
    telegram_admin_chat_id: str = ""
    telegram_admin_channel_id: str = ""
    newsapi_api_key: str = ""
    finnhub_api_key: str = ""
    alpha_vantage_api_key: str = ""
    marketaux_api_key: str = ""
    newsdata_api_key: str = ""
    news_auto_poll_enabled: bool = True
    news_google_rss_enabled: bool = False
    news_provider_timeout_seconds: int = Field(default=8, ge=1, le=30)
    news_priority_lock_seconds: int = Field(default=300, ge=30, le=3600)
    newsapi_poll_seconds: int = Field(default=900, ge=30, le=86400)
    finnhub_poll_seconds: int = Field(default=30, ge=10, le=86400)
    alpha_vantage_poll_seconds: int = Field(default=3600, ge=30, le=86400)
    marketaux_poll_seconds: int = Field(default=900, ge=30, le=86400)
    newsdata_poll_seconds: int = Field(default=432, ge=30, le=86400)
    coinpaprika_poll_seconds: int = Field(default=300, ge=30, le=86400)
    rss_poll_seconds: int = Field(default=300, ge=30, le=86400)
    max_daily_trades: int = Field(default=APEX_MAX_DAILY_SETUPS, ge=1, le=APEX_MAX_DAILY_SETUPS)
    max_per_trade_risk_pct: float = Field(default=2.0, ge=0.1, le=2.0)
    max_daily_loss_amount: float = Field(
        default=100_000_000, ge=1, le=100_000_000, allow_inf_nan=False
    )
    background_scanning_enabled: bool = True
    background_scan_interval_seconds: int = Field(default=60, ge=15, le=900)
    shoonya_daily_login_enabled: bool = True
    india_market_holidays: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
