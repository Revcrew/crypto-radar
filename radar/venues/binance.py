"""Binance futures (non-US egress via proxy).

NOTE: TZ lists /ticker/price (+premiumIndex); we use /ticker/24hr instead —
one request that also carries quoteVolume, which the turnover noise filter
requires. Leveraged tokens (UP/DOWN/BULL/BEAR) and coin-margined contracts
are excluded; only USDT-margined perps.
"""
from __future__ import annotations

from .base import Quote, VenueClient, _f

_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"

_EXCLUDED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")


class BinanceClient(VenueClient):
    name = "binance"
    uses_proxy = True

    def fetch(self) -> list[Quote]:
        resp = self._session.get(_URL, timeout=self.timeout)
        resp.raise_for_status()
        quotes: list[Quote] = []
        for item in resp.json():
            symbol = item.get("symbol") or ""
            if not symbol.endswith("USDT") or "_" in symbol:
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
                    turnover=_f(item.get("quoteVolume")),
                )
            )
        return quotes
