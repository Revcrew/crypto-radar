"""OKX: GET /market/tickers?instType=SWAP (the /public/ path 404s).

Turnover: volCcy24h is used as a USD proxy (per TZ).
"""
from __future__ import annotations

from .base import Quote, VenueClient, _f

_URL = "https://www.okx.com/api/v5/market/tickers?instType=SWAP"


class OkxClient(VenueClient):
    name = "okx"

    def fetch(self) -> list[Quote]:
        resp = self._session.get(_URL, timeout=self.timeout)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != "0":
            raise ValueError(f"OKX error code: {payload.get('code')}")
        quotes: list[Quote] = []
        for item in payload.get("data") or []:
            inst_id = item.get("instId") or ""
            if not inst_id.endswith("-SWAP"):
                continue
            price = _f(item.get("last"))
            if price is None or price <= 0:
                continue
            quotes.append(
                Quote(
                    venue=self.name,
                    symbol=inst_id,
                    base=inst_id.split("-")[0],
                    price=price,
                    turnover=_f(item.get("volCcy24h")),
                )
            )
        return quotes
