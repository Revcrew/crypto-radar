"""Hyperliquid: POST /info {"type": "metaAndAssetCtxs"}.

Response is [meta, assetCtxs]; meta.universe[i].name pairs with assetCtxs[i].
Turnover: dayNtlVlm (USD notional).
"""
from __future__ import annotations

from .base import Quote, VenueClient, _f

_URL = "https://api.hyperliquid.xyz/info"


class HyperliquidClient(VenueClient):
    name = "hyperliquid"

    def fetch(self) -> list[Quote]:
        resp = self._session.post(
            _URL, json={"type": "metaAndAssetCtxs"}, timeout=self.timeout
        )
        resp.raise_for_status()
        payload = resp.json()
        meta, ctxs = payload[0], payload[1]
        universe = meta.get("universe") or []
        quotes: list[Quote] = []
        for asset, ctx in zip(universe, ctxs):
            base = asset.get("name") or ""
            price = _f(ctx.get("markPx"))
            if not base or price is None or price <= 0:
                continue
            quotes.append(
                Quote(
                    venue=self.name,
                    symbol=base,
                    base=base,
                    price=price,
                    turnover=_f(ctx.get("dayNtlVlm")),
                )
            )
        return quotes
