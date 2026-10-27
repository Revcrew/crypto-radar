"""Non-US egress routing (TZ 1.4).

Only Binance / Bybit / KuCoin requests go through the dedicated proxy.
If a proxy venue errors twice in a row, it pauses ~120 s; the service
keeps running direct-only for the rest.
"""
from __future__ import annotations

import os
import time

PAUSE_AFTER_ERRORS = 2
PAUSE_SECONDS = 120


def _parse_dotenv(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip("'\"")
    return values


def load_proxy_url(service_dir: str) -> str | None:
    """Read PROXY_URL from proxy.env (mode 600); None if absent."""
    path = os.path.join(service_dir, "proxy.env")
    if not os.path.exists(path):
        return None
    return _parse_dotenv(path).get("PROXY_URL") or None


class ProxyRouter:
    """Tracks per-venue proxy health; pauses a venue after N consecutive errors."""

    def __init__(
        self,
        proxy_url: str | None,
        proxy_venues: tuple[str, ...] = ("binance", "bybit", "kucoin"),
        time_fn=time.time,
    ):
        self._proxy_url = proxy_url
        self._proxy_venues = set(proxy_venues)
        self._time = time_fn
        self._errors: dict[str, int] = {}
        self._paused_until: dict[str, float] = {}

    @property
    def proxies(self) -> dict | None:
        if not self._proxy_url:
            return None
        return {"http": self._proxy_url, "https": self._proxy_url}

    def is_paused(self, venue: str) -> bool:
        if venue not in self._proxy_venues:
            return False
        return self._time() < self._paused_until.get(venue, 0.0)

    def record_success(self, venue: str) -> None:
        self._errors[venue] = 0
        self._paused_until.pop(venue, None)

    def record_error(self, venue: str) -> None:
        if venue not in self._proxy_venues:
            return
        count = self._errors.get(venue, 0) + 1
        self._errors[venue] = count
        if count >= PAUSE_AFTER_ERRORS:
            self._paused_until[venue] = self._time() + PAUSE_SECONDS

    def status(self, venue: str) -> str:
        if self.is_paused(venue):
            return "PAUSED"
        if self._errors.get(venue, 0):
            return f"ERRx{self._errors[venue]}"
        return "ok"
