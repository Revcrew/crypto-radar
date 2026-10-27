"""Venue client registry."""

from .base import Quote, VenueClient
from .binance import BinanceClient
from .bingx import BingxClient
from .bybit import BybitClient
from .hyperliquid import HyperliquidClient
from .kucoin import KucoinClient
from .mexc import MexcClient
from .okx import OkxClient

# Canonical venue order (also used for alert buttons, after the origin venue).
CANONICAL_ORDER = (
    "mexc",
    "kucoin",
    "bingx",
    "binance",
    "bybit",
    "okx",
    "hyperliquid",
)

DISPLAY_NAMES = {
    "mexc": "MEXC",
    "kucoin": "KuCoin",
    "bingx": "BingX",
    "binance": "Binance",
    "bybit": "Bybit",
    "okx": "OKX",
    "hyperliquid": "Hyperliquid",
}

_CLIENTS = {
    "mexc": MexcClient,
    "kucoin": KucoinClient,
    "bingx": BingxClient,
    "binance": BinanceClient,
    "bybit": BybitClient,
    "okx": OkxClient,
    "hyperliquid": HyperliquidClient,
}


def build_venues(proxies: dict | None = None) -> list[VenueClient]:
    """Instantiate all venue clients in canonical order."""
    return [_CLIENTS[name](proxies=proxies) for name in CANONICAL_ORDER]


__all__ = [
    "CANONICAL_ORDER",
    "DISPLAY_NAMES",
    "Quote",
    "VenueClient",
    "build_venues",
]
