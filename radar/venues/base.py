"""Venue clients: one HTTP poll per exchange, all tickers in a single request."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class Quote:
    venue: str  # e.g. "mexc"
    symbol: str  # raw symbol as reported by the venue
    base: str  # normalized base asset, e.g. "BTC" (KuCoin XBT -> BTC)
    price: float
    turnover: float | None  # 24h turnover; USD, unless turnover_native
    turnover_native: bool = False  # True when turnover is in venue-native units


def _f(value) -> float | None:
    """Safe float conversion; None on garbage."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


class VenueClient(ABC):
    """One exchange. fetch() must return fresh quotes or raise."""

    name: str = "?"
    uses_proxy: bool = False  # True for Binance / Bybit / KuCoin (non-US egress)
    timeout: int = 10

    def __init__(self, proxies: dict | None = None):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "crypto-crime-radar/0.1"})
        if proxies and self.uses_proxy:
            self._session.proxies.update(proxies)

    @abstractmethod
    def fetch(self) -> list[Quote]:
        """Return latest quotes for all tickers; raise on transport/parse errors."""
