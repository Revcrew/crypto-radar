"""Spike detection over per-(venue, base) price ring buffers.

Trigger (per TZ 1.2): |d| >= 5% over 1 min OR >= 8% over 5 min OR >= 12%
over 15 min, with a 24h-turnover noise filter per window:
  - windows 5/15 min: turnover > $200K (BingX: > 5_000 native units)
  - window 1 min:     turnover > $500K (BingX: > 20_000 native units)

Freshness rules: a window is evaluated only if a reference sample exists
near (now - window) and the latest sample is fresh; gaps mean the window
is skipped rather than producing a phantom alert.

If several windows fire for one (venue, base), the SHORTEST window is
reported (most urgent signal). This choice is documented, not dictated
by the TZ.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .venues.base import Quote

HISTORY_S = 21 * 60  # ~21 minutes of price history in memory
LATEST_STALE_S = 30  # latest sample older than this -> venue data is stale
REF_TOLERANCE_S = 20  # reference sample must be within +- this of (now - window)


@dataclass(frozen=True)
class TriggerRule:
    window_s: int
    pct: float
    min_turnover_usd: float
    min_turnover_native: float


TRIGGER_RULES: tuple[TriggerRule, ...] = (
    TriggerRule(window_s=60, pct=5.0, min_turnover_usd=500_000, min_turnover_native=20_000),
    TriggerRule(window_s=300, pct=8.0, min_turnover_usd=200_000, min_turnover_native=5_000),
    TriggerRule(window_s=900, pct=12.0, min_turnover_usd=200_000, min_turnover_native=5_000),
)


@dataclass(frozen=True)
class Trigger:
    venue: str
    base: str
    pct: float  # signed percent change over window_s
    window_s: int
    price_now: float
    price_then: float
    turnover: float | None
    turnover_native: bool = False


class Detector:
    def __init__(
        self,
        rules: tuple[TriggerRule, ...] = TRIGGER_RULES,
        history_s: int = HISTORY_S,
    ):
        self._rules = rules
        self._history_s = history_s
        self._buffers: dict[tuple[str, str], deque] = {}
        self._latest: dict[tuple[str, str], Quote] = {}

    # -- ingestion ------------------------------------------------------

    def update(self, quotes: list[Quote], now: float) -> None:
        for q in quotes:
            key = (q.venue, q.base)
            buf = self._buffers.setdefault(key, deque())
            buf.append((now, q.price))
            cutoff = now - self._history_s
            while buf and buf[0][0] < cutoff:
                buf.popleft()
            self._latest[key] = q

    # -- queries ----------------------------------------------------------

    def has_base(self, venue: str, base: str) -> bool:
        return (venue, base) in self._latest

    def _reference_price(
        self, venue: str, base: str, window_s: int, now: float
    ) -> float | None:
        """Price closest to (now - window_s) within tolerance; None on gaps."""
        buf = self._buffers.get((venue, base))
        if not buf:
            return None
        if now - buf[-1][0] > LATEST_STALE_S:
            return None  # latest sample is stale -> skip venue entirely
        target = now - window_s
        best: tuple[float, float] | None = None  # (distance, price)
        for ts, price in buf:
            dist = abs(ts - target)
            if dist <= REF_TOLERANCE_S and (best is None or dist < best[0]):
                best = (dist, price)
        return best[1] if best else None

    def pct_over_window(
        self, venue: str, base: str, window_s: int, now: float
    ) -> float | None:
        ref = self._reference_price(venue, base, window_s, now)
        latest = self._latest.get((venue, base))
        if ref is None or latest is None or ref <= 0:
            return None
        return (latest.price - ref) / ref * 100.0

    # -- detection --------------------------------------------------------

    def detect(self, now: float) -> list[Trigger]:
        triggers: list[Trigger] = []
        for (venue, base), quote in self._latest.items():
            if now - self._buffer_latest_ts(venue, base) > LATEST_STALE_S:
                continue
            for rule in self._rules:  # shortest window first
                ref = self._reference_price(venue, base, rule.window_s, now)
                if ref is None or ref <= 0:
                    continue  # gap in history: skip this window, no phantom alert
                pct = (quote.price - ref) / ref * 100.0
                if abs(pct) < rule.pct:
                    continue
                if not self._turnover_ok(quote, rule):
                    continue
                triggers.append(
                    Trigger(
                        venue=venue,
                        base=base,
                        pct=pct,
                        window_s=rule.window_s,
                        price_now=quote.price,
                        price_then=ref,
                        turnover=quote.turnover,
                        turnover_native=quote.turnover_native,
                    )
                )
                break  # one trigger per (venue, base): the shortest window
        return triggers

    def _buffer_latest_ts(self, venue: str, base: str) -> float:
        buf = self._buffers.get((venue, base))
        return buf[-1][0] if buf else 0.0

    @staticmethod
    def _turnover_ok(quote: Quote, rule: TriggerRule) -> bool:
        if quote.turnover is None:
            return False  # fail closed: unknown turnover -> no alert
        if quote.turnover_native:
            return quote.turnover > rule.min_turnover_native
        return quote.turnover > rule.min_turnover_usd
