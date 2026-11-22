import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.config import Settings
from backend.app.routers import legacy


def _request(app, query):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request({
        "type": "http",
        "method": "GET",
        "path": "/legacy",
        "query_string": query,
        "headers": [(b"authorization", b"Bearer test-user-token")],
        "app": app,
    }, receive=receive)


def test_legacy_fallback_proxies_only_allowlisted_target(monkeypatch):
    observed = {}

    async def handler(request):
        observed["url"] = request.url
        observed["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"success": True})

    app = SimpleNamespace(
        state=SimpleNamespace(legacy_worker_transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(
        legacy,
        "get_settings",
        lambda: Settings(_env_file=None, legacy_worker_url="https://worker.example/"),
    )

    response = asyncio.run(legacy.legacy_fallback(_request(app, b"target=health&view=summary")))

    assert response.status_code == 200
    assert observed["url"].params["target"] == "health"
    assert observed["url"].params["view"] == "summary"
    assert observed["authorization"] == "Bearer test-user-token"


def test_legacy_fallback_rejects_unknown_targets(monkeypatch):
    monkeypatch.setattr(
        legacy,
        "get_settings",
        lambda: Settings(_env_file=None, legacy_worker_url="https://worker.example/"),
    )
    app = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(HTTPException) as error:
        asyncio.run(legacy.legacy_fallback(_request(app, b"target=https%3A%2F%2Fexample.com")))

    assert error.value.status_code == 404
