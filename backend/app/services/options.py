from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from ..brokers.base import BrokerQuote


INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30))
EXPIRY_CLOSE = time(15, 30)
MAX_OPTION_SPREAD_PCT = 2.0
INDEX_SEGMENTS = {
    "NIFTY": "NFO",
    "BANKNIFTY": "NFO",
    "FINNIFTY": "NFO",
    "MIDCPNIFTY": "NFO",
    "SENSEX": "BFO",
    "BANKEX": "BFO",
}
INDEX_SPOT_INSTRUMENTS = {
    "NIFTY": ("26000", "NSE"),
    "BANKNIFTY": ("26009", "NSE"),
    "FINNIFTY": ("26037", "NSE"),
    "MIDCPNIFTY": ("26074", "NSE"),
    "SENSEX": ("1", "BSE"),
    "BANKEX": ("12", "BSE"),
}
_BFO_ROOTS = ("SENSEX50", "SENSEX", "BANKEX")


@dataclass(frozen=True)
class OptionContract:
    underlying: str
    exchange: str
    token: str
    trading_symbol: str
    expiry: date
    option_type: str
    strike: float
    lot_size: int


def _bfo_underlying(symbol: str, trading_symbol: str) -> str:
    raw_symbol = symbol.strip().upper()
    if raw_symbol in {"SENSEX", "BANKEX"}:
        return raw_symbol
    tsym = trading_symbol.strip().upper()
    for root in _BFO_ROOTS:
        if tsym.startswith(root) and len(tsym) > len(root) and tsym[len(root)].isdigit():
            return root
    return raw_symbol


def parse_option_master(rows: list[dict[str, Any]], exchange: str) -> list[OptionContract]:
    if exchange not in {"NFO", "BFO"}:
        raise ValueError("Options are only supported on NFO or BFO")
    contracts: list[OptionContract] = []
    for row in rows:
        normalized = {str(key).strip().lower(): value for key, value in row.items()}
        trading_symbol = str(normalized.get("tradingsymbol") or "").strip()
        token = str(normalized.get("token") or "").strip()
        row_exchange = str(normalized.get("exchange") or exchange).strip().upper()
        instrument = str(normalized.get("instrument") or "").strip().upper()
        symbol = str(normalized.get("symbol") or "").strip().upper()
        option_type = str(normalized.get("optiontype") or "").strip().upper()
        if (
            row_exchange != exchange
            or instrument not in {"OPTIDX", "OPTSTK"}
            or option_type not in {"CE", "PE"}
            or not token.isdigit()
            or not trading_symbol
        ):
            continue
        underlying = _bfo_underlying(symbol, trading_symbol) if exchange == "BFO" else symbol
        if instrument == "OPTIDX" and underlying not in INDEX_SEGMENTS:
            continue
        try:
            expiry = datetime.strptime(
                str(normalized["expiry"]).strip().upper(), "%d-%b-%Y"
            ).date()
            strike = float(
                normalized.get("strikeprice") or normalized.get("strike") or 0
            )
            lot_size = int(float(normalized.get("lotsize") or 0))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(strike) or strike <= 0 or lot_size <= 0:
            continue
        contracts.append(OptionContract(
            underlying=underlying,
            exchange=exchange,
            token=token,
            trading_symbol=trading_symbol,
            expiry=expiry,
            option_type=option_type,
            strike=strike,
            lot_size=lot_size,
        ))
    return contracts


def active_expiry(
    expiries: list[date],
    *,
    now: datetime | None = None,
) -> date | None:
    current = (now or datetime.now(timezone.utc)).astimezone(INDIA_TIMEZONE)
    eligible = sorted({
        expiry for expiry in expiries
        if expiry > current.date()
        or (expiry == current.date() and current.time().replace(tzinfo=None) < EXPIRY_CLOSE)
    })
    return eligible[0] if eligible else None


def nearest_atm_itm_contracts(
    contracts: list[OptionContract],
    *,
    underlying: str,
    option_type: str,
    expiry: date,
    spot: float,
    limit: int = 5,
) -> list[OptionContract]:
    if not math.isfinite(spot) or spot <= 0 or limit < 1:
        return []
    eligible = [
        contract for contract in contracts
        if contract.underlying == underlying
        and contract.expiry == expiry
        and contract.option_type == option_type
        and (
            contract.strike <= spot if option_type == "CE"
            else contract.strike >= spot
        )
    ]
    return sorted(
        eligible,
        key=lambda contract: (
            abs(contract.strike - spot),
            contract.strike,
            contract.token,
        ),
    )[:limit]


async def select_liquid_shoonya_option(
    adapter: Any,
    *,
    underlying: str,
    direction: str,
    max_spread_pct: float = MAX_OPTION_SPREAD_PCT,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = underlying.strip().upper()
    if direction not in {"BULLISH", "BEARISH"}:
        raise ValueError("Option direction must be BULLISH or BEARISH")
    if not math.isfinite(max_spread_pct) or not 0 < max_spread_pct <= 10:
        raise ValueError("Maximum option spread must be greater than 0 and at most 10%")
    exchange = INDEX_SEGMENTS.get(root, "NFO")
    contracts = parse_option_master(await adapter.get_option_master(exchange), exchange)
    matching = [contract for contract in contracts if contract.underlying == root]
    expiries = sorted({contract.expiry for contract in matching})
    expiry = active_expiry(expiries, now=now)
    if expiry is None:
        raise RuntimeError("No active Shoonya option expiry is available for this underlying")

    spot_reference = INDEX_SPOT_INSTRUMENTS.get(root)
    if spot_reference:
        spot_token, spot_exchange = spot_reference
    else:
        spot_instrument = await adapter.search_instrument(root, "NSE")
        spot_token = str(spot_instrument.get("token") or "")
        spot_exchange = "NSE"
        if not spot_token.isdigit():
            raise RuntimeError("Shoonya returned an invalid underlying token")
    spot_quote = await adapter.get_quote(spot_token, spot_exchange)
    if not spot_quote or spot_quote.source != "SHOONYA" or spot_quote.price <= 0:
        raise RuntimeError("Shoonya underlying quote is unavailable")

    option_type = "CE" if direction == "BULLISH" else "PE"
    candidates = nearest_atm_itm_contracts(
        matching,
        underlying=root,
        option_type=option_type,
        expiry=expiry,
        spot=spot_quote.price,
    )
    if not candidates:
        raise RuntimeError("No ATM/ITM option contracts are available for the active expiry")

    quotes = await asyncio.gather(
        *(adapter.get_quote(contract.token, contract.exchange) for contract in candidates),
        return_exceptions=True,
    )
    liquid: list[tuple[tuple[float, float, float], OptionContract, BrokerQuote, float]] = []
    for contract, quote in zip(candidates, quotes):
        if not isinstance(quote, BrokerQuote) or quote.source != "SHOONYA":
            continue
        bid, ask = quote.bid, quote.ask
        if (
            bid is None or ask is None
            or not math.isfinite(bid) or not math.isfinite(ask)
            or bid <= 0 or ask < bid
            or quote.volume <= 0
        ):
            continue
        midpoint = (bid + ask) / 2
        spread_pct = (ask - bid) / midpoint * 100
        if spread_pct > max_spread_pct:
            continue
        liquid.append((
            (
                spread_pct,
                -quote.volume,
                abs(contract.strike - spot_quote.price),
            ),
            contract,
            quote,
            spread_pct,
        ))
    if not liquid:
        raise RuntimeError("No ATM/ITM option passed the bid-ask spread and liquidity checks")
    _, contract, quote, spread_pct = min(liquid, key=lambda item: item[0])
    return {
        "underlying": root,
        "direction": direction,
        "option_type": option_type,
        "exchange": contract.exchange,
        "token": contract.token,
        "trading_symbol": contract.trading_symbol,
        "expiry": contract.expiry.isoformat(),
        "strike": contract.strike,
        "lot_size": contract.lot_size,
        "spot": spot_quote.price,
        "bid": quote.bid,
        "ask": quote.ask,
        "entry_price": quote.ask,
        "spread_pct": spread_pct,
        "volume": quote.volume,
    }
