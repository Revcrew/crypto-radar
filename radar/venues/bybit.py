"""Bybit linear perps (non-US egress via proxy). Turnover: turnover24h (USDT)."""
from __future__ import annotations

from .base import Quote, VenueClient, _f

_URL = "https://api.bybit.com/v5/market/tickers?category=linear"

_EXCLUDED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")


class BybitClient(VenueClient):
    name = "bybit"
    uses_proxy = True

    def fetch(self) -> list[Quote]:
        resp = self._session.get(_URL, timeout=self.timeout)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("retCode") != 0:
            raise ValueError(f"Bybit error: {payload.get('retMsg')}")
        quotes: list[Quote] = []
        for item in (payload.get("result") or {}).get("list") or []:
            symbol = item.get("symbol") or ""
            if not symbol.endswith("USDT"):
                continue
            if symbol.endswith(_EXCLUDED_SUFFIXES):
                continue
            price = _f(item.get("lastPrice"))
            if price is None or price <= 0:
                continue
            quotes.append(
                Quote(
                    venue=self.name,
                    symbol=symbol,
                    base=symbol[: -len("USDT")],
                    price=price,
                    turnover=_f(item.get("turnover24h")),
                )
            )
        return quotes
