"""KuCoin futures (non-US egress via proxy).

Snapshot of active contracts every poll (~30 s granularity per TZ).
XBT is normalized to BTC; other symbols are <BASE>USDTM.
"""
from __future__ import annotations

from .base import Quote, VenueClient, _f

_URL = "https://api-futures.kucoin.com/api/v1/contracts/active"

_XBT = "XBT"


class KucoinClient(VenueClient):
    name = "kucoin"
    uses_proxy = True

    def fetch(self) -> list[Quote]:
        resp = self._session.get(_URL, timeout=self.timeout)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != "200000":
            raise ValueError(f"KuCoin error code: {payload.get('code')}")
        quotes: list[Quote] = []
        for item in payload.get("data") or []:
            symbol = item.get("symbol") or ""
            if not symbol.endswith("USDTM"):
                continue
            base = symbol[: -len("USDTM")]
            if base == _XBT:
                base = "BTC"
            price = _f(item.get("lastTradePrice"))
            if price is None or price <= 0:
                continue
            quotes.append(
                Quote(
                    venue=self.name,
                    symbol=symbol,
                    base=base,
                    price=price,
                    turnover=_f(item.get("turnoverOf24h")),
                )
            )
        return quotes
