from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class BrokerQuote:
    symbol: str
    exchange: str
    price: float
    high: float
    low: float
    volume: float = 0
    source: str = "UNKNOWN"
    bid: float | None = None
    ask: float | None = None


@dataclass
class BrokerOrderResult:
    accepted: bool
    broker_order_id: str | None
    status: str
    message: str = ""
    raw: dict[str, Any] | None = None


class BrokerAdapter(ABC):
    @abstractmethod
    async def login(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_quote(self, symbol: str, exchange: str) -> BrokerQuote | None:
        raise NotImplementedError

    @abstractmethod
    async def place_order(self, **kwargs: Any) -> BrokerOrderResult:
        raise NotImplementedError

    @abstractmethod
    async def get_positions(self) -> list[dict[str, Any]]:
        raise NotImplementedError
