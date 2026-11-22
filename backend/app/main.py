from contextlib import asynccontextmanager
import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
import aiohttp
import httpx
from types import SimpleNamespace

from .config import get_settings
from .integrations.database import DatabaseHub
from .integrations.firebase import FirebaseHub
from .integrations.mongo import MongoHub
from .integrations.redis import RedisCache
from .routers import (
    admin,
    automation,
    auth,
    brokers,
    engines,
    ghost_admin,
    greeks,
    health,
    legacy,
    news,
    notifications,
    orders,
    preferences,
    risk,
    scanner,
)
from .dependencies import risk_guardian
from .services.news import NewsEngine
from .services.automation import OpportunityHub
from .services.paper_lifecycle import advance_paper_position
from .services.scheduler import is_india_market_open, seconds_until_shoonya_login
from .services.scanner import UniverseScanner
from .services.risk_persistence import load_risk_state, persist_risk_state
from .brokers.coindcx import CoinDCXAdapter
from .brokers.shoonya import ShoonyaAdapter

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = PROJECT_ROOT / "static"
LEGACY_BACKUP_ROOT = PROJECT_ROOT / "LEGACY_JS_BACKUP"


def _static_asset(filename: str) -> Path:
    static_path = STATIC_ROOT / filename
    if static_path.is_file():
        return static_path
    return LEGACY_BACKUP_ROOT / filename


async def _replication_worker(database: DatabaseHub) -> None:
    while True:
        report = await database.flush_outbox()
        if report["errors"]:
            logger.warning("Database replication outbox has pending errors: %s", report["errors"])
        await asyncio.sleep(15)


async def _news_poll_worker(engine: NewsEngine) -> None:
    while True:
        await engine.poll_due()
        await asyncio.sleep(5)


async def _paper_position_worker(app: FastAPI) -> None:
    database = app.state.database
    shoonya = ShoonyaAdapter(redis=app.state.redis)
    request = SimpleNamespace(app=app)
    while True:
        lifecycle_ready = True
        try:
            positions = await database.list_open_paper_positions()
        except RuntimeError:
            logger.exception("Paper-position persistence is unavailable")
            lifecycle_ready = False
            positions = []

        for position in positions:
            try:
                if position.exchange == "COINDCX":
                    quote = await CoinDCXAdapter().get_quote(position.symbol)
                else:
                    if not position.broker_token:
                        raise RuntimeError("Paper position has no Shoonya instrument token")
                    quote = await shoonya.get_quote(position.broker_token, position.exchange)
                expected_source = "COINDCX" if position.exchange == "COINDCX" else "SHOONYA"
                if quote is None or quote.source != expected_source:
                    raise RuntimeError("Paper-position quote is unavailable or untrusted")
                lock = app.state.paper_position_locks.setdefault(
                    position.id, asyncio.Lock()
                )
                async with lock:
                    latest = await database.get_user_paper_position(
                        position.user_id, position.id
                    )
                    if latest is None or latest.status != "OPEN":
                        continue
                    current = {
                        "id": latest.id,
                        "user_id": latest.user_id,
                        "client_order_id": latest.client_order_id,
                        "symbol": latest.symbol,
                        "exchange": latest.exchange,
                        "broker_token": latest.broker_token,
                        "side": latest.side,
                        "quantity": float(latest.quantity),
                        "entry_price": float(latest.entry_price),
                        "stop_loss": float(latest.stop_loss),
                        "take_profit": float(latest.take_profit),
                        "trailing_distance": float(latest.trailing_distance),
                        "favorable_price": float(latest.favorable_price),
                        "status": latest.status,
                        "is_auto_trade": bool(latest.is_auto_trade),
                        "exit_price": float(latest.exit_price) if latest.exit_price is not None else None,
                        "realized_pnl": float(latest.realized_pnl),
                        "opened_at": latest.opened_at,
                        "closed_at": latest.closed_at,
                        "exit_reason": latest.exit_reason,
                        "manual_exit_blocked_until": latest.manual_exit_blocked_until,
                        "pnl_fx_to_inr": float(latest.pnl_fx_to_inr or 1),
                    }
                    updated = advance_paper_position(current, float(quote.price))
                    persistence = await database.upsert_paper_position(**updated)
                    if not (persistence.primary_applied or persistence.backup_applied):
                        raise RuntimeError("Paper-position update could not be persisted")
                    if updated["status"] == "CLOSED":
                        guardian = app.state.risk_guardian
                        await load_risk_state(request, latest.user_id, guardian)
                        risk_state = guardian.get_state(latest.user_id)
                        guardian.sync_pnl(
                            latest.user_id,
                            risk_state.daily_pnl_inr
                            + float(updated["realized_pnl"])
                            * float(latest.pnl_fx_to_inr or 1),
                        )
                        await persist_risk_state(
                            request,
                            latest.user_id,
                            guardian.get_state(latest.user_id),
                        )
            except (RuntimeError, ValueError, OSError, httpx.HTTPError, RedisError):
                logger.exception("Paper position could not be advanced for %s", position.symbol)
                lifecycle_ready = False

        app.state.paper_lifecycle_ready = lifecycle_ready
        await asyncio.sleep(1)


async def _shoonya_login_worker(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(seconds_until_shoonya_login())
        try:
            await ShoonyaAdapter(redis=app.state.redis).login(force=True)
            logger.info("Scheduled Shoonya session refresh completed")
        except (RuntimeError, ValueError, httpx.HTTPError, OSError, RedisError):
            logger.exception("Scheduled Shoonya session refresh failed")


async def _automation_scan_worker(app: FastAPI) -> None:
    database = app.state.database
    request = SimpleNamespace(app=app)
    while True:
        try:
            subjects = await database.list_automation_subjects(limit=100)
            markets = ["CRYPTO"]
            if is_india_market_open():
                markets.append("INDIA")
            for subject in subjects:
                record = await database.get_user_preferences(subject)
                automation_settings = (
                    (record.preferences or {}).get("automation", {})
                    if record and isinstance(record.preferences, dict)
                    else {}
                )
                if not isinstance(automation_settings, dict):
                    continue
                allowed_markets = automation_settings.get("allowedMarkets", "ALL")
                for market in markets:
                    if allowed_markets not in {"ALL", market}:
                        continue
                    try:
                        await automation.scan_for_opportunities(
                            automation.AutomationScanPayload(
                                market=market,
                                timeframe="15m",
                                limit=10,
                            ),
                            request,
                            subject,
                        )
                    except (
                        HTTPException,
                        RuntimeError,
                        ValueError,
                        OSError,
                        httpx.HTTPError,
                        aiohttp.ClientError,
                        asyncio.TimeoutError,
                        RedisError,
                    ):
                        logger.exception("Background %s market scan failed", market)
        except (RuntimeError, ValueError, OSError, SQLAlchemyError, RedisError):
            logger.exception("Background automation scan cycle failed")
        await asyncio.sleep(get_settings().background_scan_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = RedisCache()
    app.state.database = DatabaseHub()
    app.state.mongo = MongoHub()
    app.state.firebase = FirebaseHub()
    app.state.risk_guardian = risk_guardian
    app.state.opportunity_hub = OpportunityHub()
    app.state.paper_position_locks = {}
    app.state.paper_setup_locks = {}
    app.state.universe_scanner = UniverseScanner()
    app.state.paper_lifecycle_ready = False
    app.state.news_engine = NewsEngine(
        get_settings(),
        redis=app.state.redis,
        database=app.state.database,
        mongo=app.state.mongo,
        firebase=app.state.firebase,
    )
    app.state.auto_trade_enabled = False
    try:
        await app.state.mongo.ensure_indexes()
    except PyMongoError:
        logger.exception("MongoDB history indexes could not be initialized")
    replication_task = (
        asyncio.create_task(_replication_worker(app.state.database))
        if app.state.database.sessions and app.state.database.backup_sessions
        else None
    )
    paper_task = (
        asyncio.create_task(_paper_position_worker(app))
        if app.state.database.enabled
        else None
    )
    news_task = (
        asyncio.create_task(_news_poll_worker(app.state.news_engine))
        if get_settings().news_auto_poll_enabled
        and get_settings().app_env.lower() == "production"
        else None
    )
    automation_task = (
        asyncio.create_task(_automation_scan_worker(app))
        if get_settings().background_scanning_enabled
        and get_settings().app_env.lower() == "production"
        and app.state.database.enabled
        else None
    )
    shoonya_login_task = (
        asyncio.create_task(_shoonya_login_worker(app))
        if get_settings().shoonya_daily_login_enabled
        and get_settings().app_env.lower() == "production"
        and app.state.redis.enabled
        and all((
            get_settings().shoonya_api_key,
            get_settings().shoonya_user_id,
            get_settings().shoonya_password,
            get_settings().shoonya_totp_secret,
        ))
        else None
    )
    try:
        yield
    finally:
        for task in (shoonya_login_task, automation_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if news_task:
            news_task.cancel()
            try:
                await news_task
            except asyncio.CancelledError:
                pass
        if paper_task:
            paper_task.cancel()
            try:
                await paper_task
            except asyncio.CancelledError:
                pass
        if replication_task:
            replication_task.cancel()
            try:
                await replication_task
            except asyncio.CancelledError:
                pass
        await app.state.redis.close()
        await app.state.database.close()
        app.state.mongo.close()


settings = get_settings()
app = FastAPI(
    title="ARAF Institutional Trading API",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(legacy.router)
app.include_router(automation.router)
app.include_router(auth.router)
app.include_router(brokers.router)
app.include_router(notifications.router)
app.include_router(greeks.router)
app.include_router(engines.router)
app.include_router(risk.router)
app.include_router(scanner.router)
app.include_router(orders.router)
app.include_router(admin.router)
app.include_router(news.router)
app.include_router(preferences.router)
app.include_router(ghost_admin.router)


@app.get("/", include_in_schema=False)
async def frontend() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/admin", include_in_schema=False)
async def admin_frontend() -> FileResponse:
    return FileResponse(LEGACY_BACKUP_ROOT / "admin index.html")


@app.get("/admin-ghost-control", include_in_schema=False)
async def ghost_admin_frontend() -> FileResponse:
    return FileResponse(_static_asset("admin-ghost-control.html"))


@app.get("/app.js", include_in_schema=False)
async def frontend_app_js() -> FileResponse:
    return FileResponse(STATIC_ROOT / "app.js", media_type="text/javascript")


@app.get("/engines.js", include_in_schema=False)
async def frontend_engines_js() -> FileResponse:
    return FileResponse(_static_asset("engines.js"), media_type="text/javascript")


@app.get("/ghost-admin.js", include_in_schema=False)
async def ghost_admin_script() -> FileResponse:
    return FileResponse(_static_asset("ghost-admin.js"), media_type="text/javascript")


@app.get("/style.css", include_in_schema=False)
async def frontend_stylesheet() -> FileResponse:
    return FileResponse(STATIC_ROOT / "style.css", media_type="text/css")


@app.get("/manifest.json", include_in_schema=False)
async def frontend_manifest() -> FileResponse:
    return FileResponse(_static_asset("manifest.json"), media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
async def frontend_service_worker() -> FileResponse:
    return FileResponse(_static_asset("sw.js"), media_type="text/javascript")
