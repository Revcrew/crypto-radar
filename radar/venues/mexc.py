"""MEXC futures: GET contract ticker. Turnover field: amount24 (USDT)."""
from __future__ import annotations

from .base import Quote, VenueClient, _f

_URL = "https://contract.mexc.com/api/v1/contract/ticker"


class MexcClient(VenueClient):
    name = "mexc"

    def fetch(self) -> list[Quote]:
        resp = self._session.get(_URL, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json().get("data") or []
        quotes: list[Quote] = []
        for item in data:
            symbol = item.get("symbol") or ""
            if not symbol.endswith("_USDT"):
                continue
            price = _f(item.get("lastPrice"))
            if price is None or price <= 0:
                continue
            quotes.append(
                Quote(
                    venue=self.name,
                    symbol=symbol,
                    base=symbol[: -len("_USDT")],
                    price=price,
                    turnover=_f(item.get("amount24")),
                )
            )
        return quotes
