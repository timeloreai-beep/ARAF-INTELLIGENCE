from contextlib import asynccontextmanager
import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pymongo.errors import PyMongoError

from .config import get_settings
from .integrations.database import DatabaseHub
from .integrations.firebase import FirebaseHub
from .integrations.mongo import MongoHub
from .integrations.redis import RedisCache
from .routers import admin, auth, brokers, greeks, health, notifications, orders, risk, scanner
from .dependencies import risk_guardian

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


async def _replication_worker(database: DatabaseHub) -> None:
    while True:
        report = await database.flush_outbox()
        if report["errors"]:
            logger.warning("Database replication outbox has pending errors: %s", report["errors"])
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = RedisCache()
    app.state.database = DatabaseHub()
    app.state.mongo = MongoHub()
    app.state.firebase = FirebaseHub()
    app.state.risk_guardian = risk_guardian
    try:
        await app.state.mongo.ensure_indexes()
    except PyMongoError:
        logger.exception("MongoDB history indexes could not be initialized")
    replication_task = (
        asyncio.create_task(_replication_worker(app.state.database))
        if app.state.database.sessions and app.state.database.backup_sessions
        else None
    )
    try:
        yield
    finally:
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
app.include_router(auth.router)
app.include_router(brokers.router)
app.include_router(notifications.router)
app.include_router(greeks.router)
app.include_router(risk.router)
app.include_router(scanner.router)
app.include_router(orders.router)
app.include_router(admin.router)


@app.get("/", include_in_schema=False)
async def frontend() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "index.html")


@app.get("/admin", include_in_schema=False)
async def admin_frontend() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "admin index.html")


@app.get("/app.js", include_in_schema=False)
async def frontend_app_js() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "app.js", media_type="text/javascript")


@app.get("/engines.js", include_in_schema=False)
async def frontend_engines_js() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "engines.js", media_type="text/javascript")


@app.get("/manifest.json", include_in_schema=False)
async def frontend_manifest() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
async def frontend_service_worker() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "sw.js", media_type="text/javascript")
