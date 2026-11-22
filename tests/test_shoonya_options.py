import asyncio
from datetime import datetime, timezone, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.brokers.base import BrokerQuote
from backend.app.brokers.shoonya import ShoonyaAdapter
from backend.app.dependencies import current_subject
from backend.app.routers import greeks
from backend.app.services.options import (
    INDEX_SEGMENTS,
    active_expiry,
    parse_option_master,
    select_liquid_shoonya_option,
)


IST = timezone(timedelta(hours=5, minutes=30))


def _nfo_rows():
    return [
        {
            "Exchange": "NFO",
            "Token": "1001",
            "LotSize": "65",
            "Symbol": "NIFTY",
            "TradingSymbol": "NIFTY26NOV2625900CE",
            "Expiry": "26-NOV-2026",
            "Instrument": "OPTIDX",
            "OptionType": "CE",
            "StrikePrice": "25900",
        },
        {
            "Exchange": "NFO",
            "Token": "1002",
            "LotSize": "65",
            "Symbol": "NIFTY",
            "TradingSymbol": "NIFTY26NOV2626000CE",
            "Expiry": "26-NOV-2026",
            "Instrument": "OPTIDX",
            "OptionType": "CE",
            "StrikePrice": "26000",
        },
        {
            "Exchange": "NFO",
            "Token": "1003",
            "LotSize": "65",
            "Symbol": "NIFTY",
            "TradingSymbol": "NIFTY03DEC2626000CE",
            "Expiry": "03-DEC-2026",
            "Instrument": "OPTIDX",
            "OptionType": "CE",
            "StrikePrice": "26000",
        },
    ]


def _bfo_rows():
    return [
        {
            "Exchange": "BFO",
            "Token": "2001",
            "LotSize": "20",
            "Symbol": "IDX",
            "TradingSymbol": "SENSEX27NOV2685000PE",
            "Expiry": "27-NOV-2026",
            "Instrument": "OPTIDX",
            "OptionType": "PE",
            "StrikePrice": "85000",
        },
        {
            "Exchange": "BFO",
            "Token": "2002",
            "LotSize": "20",
            "Symbol": "IDX",
            "TradingSymbol": "SENSEX27NOV2685100PE",
            "Expiry": "27-NOV-2026",
            "Instrument": "OPTIDX",
            "OptionType": "PE",
            "StrikePrice": "85100",
        },
        {
            "Exchange": "BFO",
            "Token": "2003",
            "LotSize": "20",
            "Symbol": "IDX",
            "TradingSymbol": "SENSEX5027NOV2685000PE",
            "Expiry": "27-NOV-2026",
            "Instrument": "OPTIDX",
            "OptionType": "PE",
            "StrikePrice": "85000",
        },
    ]


def test_option_master_maps_exact_nfo_and_bfo_symbols_and_excludes_sensex50():
    nfo = parse_option_master(_nfo_rows(), "NFO")
    bfo = parse_option_master(_bfo_rows(), "BFO")

    assert [(item.token, item.trading_symbol, item.underlying) for item in nfo] == [
        ("1001", "NIFTY26NOV2625900CE", "NIFTY"),
        ("1002", "NIFTY26NOV2626000CE", "NIFTY"),
        ("1003", "NIFTY03DEC2626000CE", "NIFTY"),
    ]
    assert [(item.token, item.trading_symbol, item.underlying) for item in bfo] == [
        ("2001", "SENSEX27NOV2685000PE", "SENSEX"),
        ("2002", "SENSEX27NOV2685100PE", "SENSEX"),
    ]
    assert all(item.exchange == "BFO" for item in bfo)


def test_selector_supports_all_requested_index_roots():
    assert INDEX_SEGMENTS == {
        "NIFTY": "NFO",
        "BANKNIFTY": "NFO",
        "FINNIFTY": "NFO",
        "MIDCPNIFTY": "NFO",
        "SENSEX": "BFO",
        "BANKEX": "BFO",
    }


def test_active_expiry_uses_master_dates_and_skips_expiry_after_close():
    expiries = [
        datetime(2026, 11, 26).date(),
        datetime(2026, 11, 27).date(),
    ]
    before_close = datetime(2026, 11, 26, 10, 0, tzinfo=IST)
    after_close = datetime(2026, 11, 26, 15, 31, tzinfo=IST)

    assert active_expiry(expiries, now=before_close) == expiries[0]
    assert active_expiry(expiries, now=after_close) == expiries[1]


class _FakeShoonya:
    def __init__(self, exchange, rows, quotes, spot, spot_token=None):
        self.exchange = exchange
        self.rows = rows
        self.quotes = quotes
        self.spot = spot
        self.spot_token = spot_token
        self.quote_requests = []

    async def get_option_master(self, exchange):
        assert exchange == self.exchange
        return self.rows

    async def search_instrument(self, underlying, exchange):
        assert exchange == "NSE"
        assert underlying == "RELIANCE"
        return {"token": self.spot_token}

    async def get_quote(self, token, exchange):
        self.quote_requests.append((token, exchange))
        if token in {"26000", "1", self.spot_token}:
            return self.spot
        return self.quotes.get(token)


def test_bullish_nifty_selects_liquid_nfo_call_with_broker_symbol():
    adapter = _FakeShoonya(
        "NFO",
        _nfo_rows(),
        {
            "1001": BrokerQuote("1001", "NFO", 121, 121, 120, 500, "SHOONYA", 120, 121),
            "1002": BrokerQuote("1002", "NFO", 102, 102, 100, 100, "SHOONYA", 100, 102),
        },
        BrokerQuote("26000", "NSE", 26020, 26030, 26000, 0, "SHOONYA"),
        spot_token="26000",
    )
    now = datetime(2026, 11, 21, 11, 0, tzinfo=IST)

    selected = asyncio.run(select_liquid_shoonya_option(
        adapter, underlying="NIFTY", direction="BULLISH", now=now
    ))

    assert selected["exchange"] == "NFO"
    assert selected["token"] == "1001"
    assert selected["trading_symbol"] == "NIFTY26NOV2625900CE"
    assert selected["option_type"] == "CE"
    assert selected["expiry"] == "2026-11-26"
    assert selected["entry_price"] == 121
    assert selected["spread_pct"] < 2


def test_bearish_sensex_selects_bfo_put_and_spread_gate_fails_closed():
    adapter = _FakeShoonya(
        "BFO",
        _bfo_rows(),
        {
            "2001": BrokerQuote("2001", "BFO", 205, 205, 200, 200, "SHOONYA", 200, 205),
            "2002": BrokerQuote("2002", "BFO", 221, 221, 220, 100, "SHOONYA", 220, 221),
        },
        BrokerQuote("1", "BSE", 85050, 85100, 85000, 0, "SHOONYA"),
        spot_token="1",
    )
    now = datetime(2026, 11, 21, 11, 0, tzinfo=IST)

    selected = asyncio.run(select_liquid_shoonya_option(
        adapter, underlying="SENSEX", direction="BEARISH", now=now
    ))
    assert selected["exchange"] == "BFO"
    assert selected["token"] == "2002"
    assert selected["trading_symbol"] == "SENSEX27NOV2685100PE"
    assert selected["option_type"] == "PE"
    assert selected["entry_price"] == 221

    adapter.quotes["2002"] = BrokerQuote(
        "2002", "BFO", 240, 240, 200, 100, "SHOONYA", 200, 240
    )
    with pytest.raises(RuntimeError, match="spread and liquidity"):
        asyncio.run(select_liquid_shoonya_option(
            adapter,
            underlying="SENSEX",
            direction="BEARISH",
            max_spread_pct=1,
            now=now,
        ))


def test_liquid_nse_stock_option_uses_search_token_and_official_contract_symbol():
    rows = [{
        "Exchange": "NFO",
        "Token": "3001",
        "LotSize": "250",
        "Symbol": "RELIANCE",
        "TradingSymbol": "RELIANCE26NOV261400CE",
        "Expiry": "26-NOV-2026",
        "Instrument": "OPTSTK",
        "OptionType": "CE",
        "StrikePrice": "1400",
    }]
    adapter = _FakeShoonya(
        "NFO",
        rows,
        {
            "3001": BrokerQuote(
                "3001", "NFO", 26, 26, 25.5, 1200, "SHOONYA", 25.5, 26
            )
        },
        BrokerQuote("5001", "NSE", 1405, 1410, 1400, 500_000, "SHOONYA"),
        spot_token="5001",
    )

    selected = asyncio.run(select_liquid_shoonya_option(
        adapter,
        underlying="RELIANCE",
        direction="BULLISH",
        now=datetime(2026, 11, 21, 11, 0, tzinfo=IST),
    ))

    assert selected["exchange"] == "NFO"
    assert selected["underlying"] == "RELIANCE"
    assert selected["trading_symbol"] == "RELIANCE26NOV261400CE"
    assert selected["lot_size"] == 250
    assert selected["token"] == "3001"


def test_option_selector_route_is_authenticated_and_read_only(monkeypatch):
    async def login(self):
        return {"stat": "Ok"}

    async def select(*args, **kwargs):
        assert kwargs["underlying"] == "BANKEX"
        assert kwargs["direction"] == "BEARISH"
        return {
            "exchange": "BFO",
            "token": "2002",
            "trading_symbol": "BANKEX27NOV2659000PE",
            "option_type": "PE",
            "spread_pct": 1.0,
        }

    async def forbidden_order(*args, **kwargs):
        raise AssertionError("Option selection must not submit orders")

    monkeypatch.setattr(ShoonyaAdapter, "login", login)
    monkeypatch.setattr(ShoonyaAdapter, "place_order", forbidden_order)
    monkeypatch.setattr(greeks, "select_liquid_shoonya_option", select)
    app = FastAPI()
    app.include_router(greeks.router)
    app.dependency_overrides[current_subject] = lambda: "option-user"

    with TestClient(app) as client:
        response = client.post(
            "/options/select",
            json={"underlying": "BANKEX", "direction": "BEARISH"},
        )

    assert response.status_code == 200
    assert response.json()["read_only"] is True
    assert response.json()["selection"]["exchange"] == "BFO"
    assert response.json()["selection"]["trading_symbol"] == "BANKEX27NOV2659000PE"


def test_option_selector_route_rejects_anonymous_requests():
    app = FastAPI()
    app.include_router(greeks.router)

    with TestClient(app) as client:
        response = client.post(
            "/options/select",
            json={"underlying": "NIFTY", "direction": "BULLISH"},
        )

    assert response.status_code == 401
