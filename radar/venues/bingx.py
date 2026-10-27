"""BingX swap: GET ticker. Volume is in NATIVE units (not USDT)."""
from __future__ import annotations

from .base import Quote, VenueClient, _f

_URL = "https://open-api.bingx.com/openApi/swap/v2/quote/ticker"


class BingxClient(VenueClient):
    name = "bingx"

    def fetch(self) -> list[Quote]:
        resp = self._session.get(_URL, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json().get("data") or []
        quotes: list[Quote] = []
        for item in data:
            symbol = item.get("symbol") or ""
            if not symbol.endswith("-USDT"):
                continue
            price = _f(item.get("lastPrice"))
            if price is None or price <= 0:
                continue
            quotes.append(
                Quote(
                    venue=self.name,
                    symbol=symbol,
                    base=symbol[: -len("-USDT")],
                    price=price,
                    turnover=_f(item.get("volume")),
                    turnover_native=True,
                )
            )
        return quotes
