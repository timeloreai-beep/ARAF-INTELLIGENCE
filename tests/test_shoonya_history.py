import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import parse_qs
import json
import zipfile

import httpx

from backend.app.brokers.shoonya import ShoonyaAdapter
from backend.app.config import Settings


def _adapter(monkeypatch, handler):
    monkeypatch.setattr(
        "backend.app.brokers.shoonya.get_settings",
        lambda: Settings(
            _env_file=None,
            shoonya_api_key="test-key",
            shoonya_user_id="test-user",
            shoonya_password="test-password",
            shoonya_totp_secret="JBSWY3DPEHPK3PXP",
        ),
    )
    adapter = ShoonyaAdapter(transport=httpx.MockTransport(handler))
    adapter.session_token = "test-session"
    return adapter


def test_shoonya_search_matches_equity_suffix_without_ambiguous_fallback(monkeypatch):
    async def handler(request):
        body = json.loads(parse_qs(request.content.decode())["jData"][0])
        assert body["exch"] == "NSE"
        return httpx.Response(200, json={
            "values": [
                {"exch": "NSE", "tsym": "RELIANCE-EQ", "token": "2885"},
                {"exch": "BSE", "tsym": "RELIANCE", "token": "500325"},
            ]
        })

    adapter = _adapter(monkeypatch, handler)
    instrument = asyncio.run(adapter.search_instrument("RELIANCE", "NSE"))

    assert instrument["token"] == "2885"
    assert instrument["tsym"] == "RELIANCE-EQ"


def test_shoonya_historical_candles_exclude_open_and_invalid_bars(monkeypatch):
    india_tz = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(india_tz)
    current_open = now_ist.replace(minute=(now_ist.minute // 5) * 5, second=0, microsecond=0)
    previous_open = current_open - timedelta(minutes=5)

    def row(opened, *, close=101, volume=10):
        return {
            "time": opened.strftime("%d-%m-%Y %H:%M:%S"),
            "into": "100",
            "inth": "102",
            "intl": "99",
            "intc": str(close),
            "intv": str(volume),
        }

    async def handler(request):
        form = parse_qs(request.content.decode())
        payload = json.loads(form["jData"][0])
        assert request.url.path.endswith("/TPSeries")
        assert payload["intrv"] == "5"
        return httpx.Response(200, json=[
            row(previous_open),
            row(current_open),
            row(previous_open - timedelta(minutes=5), close=120),
        ])

    adapter = _adapter(monkeypatch, handler)
    candles = asyncio.run(
        adapter.get_historical_candles("NSE", "2885", interval="5m", limit=30)
    )

    assert len(candles) == 1
    assert candles[0]["close"] == 101
    assert candles[0]["is_closed"] is True
    assert candles[0]["close_time"] <= int(datetime.now(timezone.utc).timestamp() * 1000)


def test_shoonya_option_master_download_parses_official_zip_header(monkeypatch):
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr(
            "NFO_symbols.txt",
            "Exchange,Token,LotSize,Symbol,TradingSymbol,Expiry,Instrument,OptionType,StrikePrice,TickSize,\n"
            "NFO,12345,65,NIFTY,NIFTY26NOV2626000CE,26-NOV-2026,OPTIDX,CE,26000,0.05,\n",
        )

    async def handler(request):
        assert request.url == "https://api.shoonya.com/NFO_symbols.txt.zip"
        return httpx.Response(200, content=archive.getvalue())

    adapter = _adapter(monkeypatch, handler)
    rows = asyncio.run(adapter.get_option_master("NFO", refresh=True))

    assert rows[0]["TradingSymbol"] == "NIFTY26NOV2626000CE"
    assert rows[0]["Token"] == "12345"


def test_shoonya_quote_exposes_valid_best_bid_and_ask(monkeypatch):
    async def handler(request):
        return httpx.Response(200, json={
            "stat": "Ok",
            "lp": "101",
            "h": "102",
            "l": "100",
            "vol": "500",
            "bp1": "100.5",
            "sp1": "101.5",
        })

    adapter = _adapter(monkeypatch, handler)
    quote = asyncio.run(adapter.get_quote("12345", "NFO"))

    assert quote is not None
    assert quote.bid == 100.5
    assert quote.ask == 101.5


def test_shoonya_quote_refreshes_an_expired_session_once(monkeypatch):
    paths = []

    async def handler(request):
        paths.append(request.url.path.rsplit("/", 1)[-1])
        if request.url.path.endswith("/GetQuotes") and paths.count("GetQuotes") == 1:
            return httpx.Response(
                200,
                json={"stat": "Not_Ok", "emsg": "Session Expired : Invalid Token"},
            )
        if request.url.path.endswith("/QuickAuth"):
            return httpx.Response(200, json={"stat": "Ok", "susertoken": "new-session"})
        return httpx.Response(
            200,
            json={"stat": "Ok", "lp": "101", "h": "102", "l": "100", "vol": "500"},
        )

    adapter = _adapter(monkeypatch, handler)
    adapter.session_token = "expired-session"

    quote = asyncio.run(adapter.get_quote("12345", "NSE"))

    assert quote is not None
    assert quote.price == 101
    assert paths == ["GetQuotes", "QuickAuth", "GetQuotes"]
