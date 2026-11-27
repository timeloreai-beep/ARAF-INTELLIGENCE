from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.brokers.base import BrokerQuote
from backend.app.config import Settings
from backend.app.dependencies import current_subject, get_risk_guardian
from backend.app.routers import brokers, orders
from backend.app.services.risk import RiskGuardian


class _DisabledRedis:
    enabled = False


class _DisabledDatabase:
    enabled = False


class _DisabledMongo:
    enabled = False


def _client(authenticated: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(brokers.router)
    app.state.redis = _DisabledRedis()
    app.state.database = _DisabledDatabase()
    app.state.mongo = _DisabledMongo()
    if authenticated:
        app.dependency_overrides[current_subject] = lambda: "test-user"
    return TestClient(app)


def test_shoonya_quote_endpoint_is_authenticated_and_read_only(monkeypatch):
    async def quote(self, symbol: str, exchange: str):
        return BrokerQuote(symbol, exchange, 100.5, 101, 99, 500, "SHOONYA")

    async def forbidden_order(*args, **kwargs):
        raise AssertionError("quote endpoint must never execute orders")

    monkeypatch.setattr(brokers.ShoonyaAdapter, "get_quote", quote)
    monkeypatch.setattr(brokers.ShoonyaAdapter, "place_order", forbidden_order)

    with _client() as client:
        response = client.post(
            "/brokers/test/shoonya/quote",
            json={"symbol": "2885", "exchange": "NSE"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["read_only"] is True
    assert data["quote"]["price"] == 100.5
    assert data["persistence"]["postgres"] == "not_configured"


def test_coindcx_quote_endpoint_is_authenticated_and_read_only(monkeypatch):
    async def quote(self, symbol: str, exchange: str = "COINDCX"):
        return BrokerQuote("B-BTC_USDT", "COINDCX", 65000, 65000, 65000, source="COINDCX")

    monkeypatch.setattr(brokers.CoinDCXAdapter, "get_quote", quote)

    with _client() as client:
        response = client.post(
            "/brokers/test/coindcx/quote",
            json={"symbol": "BTCUSDT"},
        )

    assert response.status_code == 200
    assert response.json()["read_only"] is True
    assert response.json()["quote"]["symbol"] == "B-BTC_USDT"


def test_coindcx_positions_are_read_only_and_normalized(monkeypatch):
    async def positions(self):
        return [
            {
                "pair": "BTCUSDT",
                "active_pos": "-0.25",
                "avg_price": "64000",
                "unrealized_pnl": "125",
            }
        ]

    monkeypatch.setattr(brokers.CoinDCXAdapter, "get_positions", positions)

    with _client() as client:
        response = client.get("/brokers/test/coindcx/positions")

    assert response.status_code == 200
    data = response.json()
    assert data["read_only"] is True
    assert data["positions"][0]["side"] == "SHORT"
    assert data["positions"][0]["quantity"] == 0.25
    assert data["positions"][0]["exchange"] == "COINDCX"


def test_quote_endpoints_reject_unauthenticated_requests():
    with _client(authenticated=False) as client:
        response = client.post(
            "/brokers/test/coindcx/quote",
            json={"symbol": "BTCUSDT"},
        )

    assert response.status_code == 401


def test_live_trading_setting_defaults_to_false_but_can_be_enabled():
    settings = Settings(_env_file=None)
    assert settings.live_trading_enabled is False
    assert Settings(_env_file=None, live_trading_enabled=True).live_trading_enabled is True


def test_paper_entry_fails_closed_without_position_persistence():
    app = FastAPI()
    app.include_router(orders.router)
    app.state.redis = _DisabledRedis()
    app.state.database = _DisabledDatabase()
    app.state.mongo = _DisabledMongo()
    app.dependency_overrides[current_subject] = lambda: "paper-user"
    app.dependency_overrides[get_risk_guardian] = RiskGuardian

    payload = {
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "side": "BUY",
        "quantity": 1,
        "operation": "ENTRY",
        "mode": "PAPER",
        "stop_loss": 99,
        "entry_price": 100,
        "client_order_id": "paper-order-001",
    }
    with TestClient(app) as client:
        first = client.post("/orders/place", json=payload)

    assert first.status_code == 503
    assert first.json()["detail"]["code"] == "PAPER_POSITION_PERSISTENCE_UNAVAILABLE"
