import asyncio

from backend.app.services import scanner as scanner_module
from backend.app.services.scanner import UniverseScanner


def test_crypto_scanner_returns_highest_turnover_usdt_pairs(monkeypatch):
    rows = [
        {"symbol": "BTCUSDT", "lastPrice": "50000", "highPrice": "51000", "lowPrice": "49000", "priceChangePercent": "1", "volume": "10", "quoteVolume": "100000000"},
        {"symbol": "ETHUSDT", "lastPrice": "3000", "highPrice": "3100", "lowPrice": "2900", "priceChangePercent": "2", "volume": "20", "quoteVolume": "300000000"},
        {"symbol": "SOLUSDT", "lastPrice": "150", "highPrice": "160", "lowPrice": "140", "priceChangePercent": "3", "volume": "30", "quoteVolume": "200000000"},
        {"symbol": "BTCUPUSDT", "lastPrice": "1", "highPrice": "1", "lowPrice": "1", "priceChangePercent": "0", "volume": "1", "quoteVolume": "500000000"},
    ]

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        async def json(self):
            return rows

    class ClientSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(scanner_module.aiohttp, "ClientSession", ClientSession)
    result = asyncio.run(UniverseScanner().scan_crypto_binance(limit=2))

    assert [item["symbol"] for item in result] == ["ETHUSDT", "SOLUSDT"]
    assert all(item["data_source"] == "binance" for item in result)


def test_crypto_scanner_falls_back_to_coingecko_when_binance_is_unavailable(monkeypatch):
    calls = []

    class Response:
        def __init__(self, url):
            self.url = url

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            if "binance.com" in self.url:
                raise scanner_module.aiohttp.ClientError("Binance unavailable")

        async def json(self):
            return [
                {
                    "symbol": "btc",
                    "current_price": 50000,
                    "high_24h": 51000,
                    "low_24h": 49000,
                    "price_change_percentage_24h": -2,
                    "total_volume": 100000000,
                }
            ]

    class ClientSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            calls.append(url)
            return Response(url)

    monkeypatch.setattr(scanner_module.aiohttp, "ClientSession", ClientSession)
    result = asyncio.run(UniverseScanner().scan_crypto(limit=10))

    assert [item["symbol"] for item in result] == ["BTCUSDT"]
    assert result[0]["data_source"] == "coingecko"
    assert "api.coingecko.com" in calls[1]


def test_crypto_scanner_falls_back_to_coinpaprika_when_earlier_sources_fail(monkeypatch):
    calls = []

    class Response:
        def __init__(self, url):
            self.url = url

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            if "coinpaprika.com" not in self.url:
                raise scanner_module.aiohttp.ClientError("Provider unavailable")

        async def json(self):
            return [
                {
                    "symbol": "BTC",
                    "quotes": {
                        "USD": {
                            "price": 50000,
                            "volume_24h": 100000000,
                            "percent_change_24h": 1.5,
                        }
                    },
                }
            ]

    class ClientSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            calls.append(url)
            return Response(url)

    monkeypatch.setattr(scanner_module.aiohttp, "ClientSession", ClientSession)
    result = asyncio.run(UniverseScanner().scan_crypto(limit=10))

    assert [item["symbol"] for item in result] == ["BTCUSDT"]
    assert result[0]["data_source"] == "coinpaprika"
    assert "api.binance.com" in calls[0]
    assert "api.coingecko.com" in calls[1]
    assert "api.coinpaprika.com" in calls[2]
