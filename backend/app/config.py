from functools import lru_cache
import warnings

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_secret: str = ""
    jwt_secret: str = ""
    access_token_ttl_minutes: int = 30
    pin_ttl_minutes: int = 720
    cors_origins: str = "http://localhost:3000"
    live_trading_enabled: bool = False

    supabase_database_url: str = ""
    neon_database_url: str = ""
    mongodb_atlas_url: str = ""
    mongodb_database: str = "araf"
    upstash_redis_url: str = ""
    firebase_project_id: str = ""
    firebase_service_account_json: str = ""
    gmail_bridge_url: str = ""
    gmail_bridge_secret: str = ""

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
    max_daily_trades: int = 5
    max_per_trade_risk_pct: float = 2.0
    max_daily_loss_pct: float = 2.5

    @model_validator(mode="after")
    def enforce_paper_only(self) -> "Settings":
        if self.live_trading_enabled:
            warnings.warn(
                "LIVE_TRADING_ENABLED=true is ignored; this backend build is paper-only",
                RuntimeWarning,
                stacklevel=2,
            )
            self.live_trading_enabled = False
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
