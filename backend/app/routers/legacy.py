import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["legacy-fallback"])

LEGACY_TARGETS = frozenset({
    "save_master_user",
    "get_master_user",
    "verify_pin",
    "set_master_pin",
    "login_with_trader_uid",
    "send_real_otp",
    "verify_server_otp",
    "submit_kyc",
    "logout_session",
    "search_stock",
    "historical_candles",
    "coindcx_market_feed",
    "master_market_feed",
    "public_market_feed",
    "deep_scanner",
    "asset_details",
    "fast_exit",
    "broker_positions",
    "reconcile_order",
    "shoonya_auto_login",
    "get_broker_vault_status",
    "delete_broker_vault",
    "save_broker_vault",
    "broker_balances",
    "broker_holdings",
    "broker_orders",
    "ai_guardian",
    "health",
    "translate_text",
    "news_article",
    "ipo_feed",
    "ipo_news_feed",
})
_FORWARDED_HEADERS = {
    "authorization",
    "content-type",
    "origin",
    "x-admin-token",
    "x-client-order-id",
    "x-pin-token",
}


@router.api_route("/legacy", methods=["GET", "POST"])
async def legacy_fallback(request: Request) -> Response:
    target = request.query_params.get("target", "")
    if target not in LEGACY_TARGETS:
        raise HTTPException(status_code=404, detail="Legacy route is not available")

    settings = get_settings()
    try:
        worker = httpx.URL(settings.legacy_worker_url)
    except httpx.InvalidURL as exc:
        raise HTTPException(status_code=503, detail="Legacy Worker endpoint is not configured safely") from exc
    if (
        worker.scheme != "https"
        or not worker.host
        or worker.username
        or worker.password
        or worker.query
        or worker.fragment
    ):
        raise HTTPException(status_code=503, detail="Legacy Worker endpoint is not configured safely")

    body = await request.body()
    if len(body) > 65536:
        raise HTTPException(status_code=413, detail="Legacy request payload is too large")
    params = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key != "target"
    ]
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() in _FORWARDED_HEADERS
    }

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15),
            transport=getattr(request.app.state, "legacy_worker_transport", None),
            follow_redirects=False,
        ) as client:
            upstream = await client.request(
                request.method,
                str(worker),
                params=[("target", target), *params],
                content=body or None,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        logger.warning("Legacy Worker request failed for target %s: %s", target, type(exc).__name__)
        raise HTTPException(status_code=502, detail="Legacy fallback service is unavailable") from exc

    response_headers = {"Cache-Control": "no-store"}
    content_type = upstream.headers.get("content-type")
    if content_type:
        response_headers["Content-Type"] = content_type
    correlation_id = upstream.headers.get("x-correlation-id")
    if correlation_id:
        response_headers["X-Correlation-Id"] = correlation_id
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
    )
