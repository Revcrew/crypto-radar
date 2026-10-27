"""Alert aggregation and Telegram message formatting.

- One asset firing on several venues in one cycle = ONE alert.
- Origin venue = venue with max |%| over its triggered window.
- Cooldown per base asset: 30 minutes.
- Message: HTML + inline URL buttons. First button = origin venue (marked),
  then canonical order MEXC -> KuCoin -> BingX -> Binance -> Bybit -> OKX ->
  Hyperliquid; only venues where the asset actually trades.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field

from .detector import Detector
from .venues import CANONICAL_ORDER, DISPLAY_NAMES

COOLDOWN_S = 30 * 60

_WINDOW_LABELS = {60: "1 мин", 300: "5 мин", 900: "15 мин"}


@dataclass(frozen=True)
class Alert:
    base: str
    direction: str  # "pump" | "dump"
    pct: float
    window_s: int
    origin: str  # venue name
    price_before: float
    price_after: float
    turnover: float | None
    turnover_native: bool
    others: tuple[tuple[str, float], ...]  # (venue display, pct) on other venues
    venues_traded: tuple[str, ...]  # canonical order, origin excluded
    ts: float


class AlertAggregator:
    def __init__(self, cooldown_s: int = COOLDOWN_S):
        self._cooldown_s = cooldown_s
        self._last_alert: dict[str, float] = {}

    def aggregate(
        self, triggers, detector: Detector, now: float
    ) -> list[Alert]:
        by_base: dict[str, list] = {}
        for t in triggers:
            by_base.setdefault(t.base, []).append(t)

        alerts: list[Alert] = []
        for base, group in by_base.items():
            last = self._last_alert.get(base)
            if last is not None and now - last < self._cooldown_s:
                continue  # cooldown
            origin_trigger = max(group, key=lambda t: abs(t.pct))
            direction = "pump" if origin_trigger.pct > 0 else "dump"

            others: list[tuple[str, float]] = []
            venues_traded: list[str] = []
            for venue in CANONICAL_ORDER:
                if venue == origin_trigger.venue:
                    continue
                if not detector.has_base(venue, base):
                    continue
                venues_traded.append(venue)
                pct = detector.pct_over_window(
                    venue, base, origin_trigger.window_s, now
                )
                if pct is not None:
                    others.append((DISPLAY_NAMES[venue], pct))

            self._last_alert[base] = now
            alerts.append(
                Alert(
                    base=base,
                    direction=direction,
                    pct=origin_trigger.pct,
                    window_s=origin_trigger.window_s,
                    origin=origin_trigger.venue,
                    price_before=origin_trigger.price_then,
                    price_after=origin_trigger.price_now,
                    turnover=origin_trigger.turnover,
                    turnover_native=origin_trigger.turnover_native,
                    others=tuple(others),
                    venues_traded=tuple(venues_traded),
                    ts=now,
                )
            )
        return alerts


# -- formatting ------------------------------------------------------------


def _fmt_money(turnover: float | None, native: bool) -> str:
    if turnover is None:
        return "н/д"
    if native:
        return f"{turnover:,.0f} нативных ед."
    if turnover >= 1_000_000:
        return f"${turnover / 1_000_000:.1f}M"
    if turnover >= 1_000:
        return f"${turnover / 1_000:.0f}K"
    return f"${turnover:,.0f}"


def _fmt_price(price: float) -> str:
    return f"{price:,.2f}"


def venue_url(venue: str, base: str) -> str:
    if venue == "mexc":
        return f"https://www.mexc.com/futures/{base}_USDT"
    if venue == "kucoin":
        symbol = "XBTUSDTM" if base == "BTC" else f"{base}USDTM"
        return f"https://www.kucoin.com/futures/trade/{symbol}"
    if venue == "bingx":
        return f"https://bingx.com/en/perpetual/{base}-USDT"
    if venue == "binance":
        return f"https://www.binance.com/en/futures/{base}USDT"
    if venue == "bybit":
        return f"https://www.bybit.com/trade/usdt/{base}USDT"
    if venue == "okx":
        return f"https://www.okx.com/trade-swap/{base.lower()}-usdt-swap"
    if venue == "hyperliquid":
        return f"https://app.hyperliquid.xyz/trade/{base}"
    raise ValueError(f"unknown venue: {venue}")


def format_alert(alert: Alert) -> tuple[str, list[list[dict]]]:
    """Return (html_text, inline_keyboard_rows)."""
    emoji = "🟢" if alert.direction == "pump" else "🔴"
    word = "ПАМП" if alert.direction == "pump" else "ДАМП"
    window_label = _WINDOW_LABELS.get(alert.window_s, f"{alert.window_s}с")

    lines = [
        f"{emoji} <b>{word} {html.escape(alert.base)}</b>",
        f"<b>{alert.pct:+.2f}%</b> за {window_label}",
        f"📍 Источник: {DISPLAY_NAMES[alert.origin]}",
        f"Цена: {_fmt_price(alert.price_before)} → {_fmt_price(alert.price_after)} USDT",
        f"Оборот 24ч (источник): {_fmt_money(alert.turnover, alert.turnover_native)}",
    ]
    if alert.others:
        others = " · ".join(f"{name} {pct:+.1f}%" for name, pct in alert.others)
        lines.append(f"Другие биржи: {others}")
    text = "\n".join(lines)

    keyboard: list[list[dict]] = [
        [{"text": f"📍 {DISPLAY_NAMES[alert.origin]}", "url": venue_url(alert.origin, alert.base)}]
    ]
    for venue in CANONICAL_ORDER:
        if venue == alert.origin or venue not in alert.venues_traded:
            continue
        keyboard.append(
            [{"text": DISPLAY_NAMES[venue], "url": venue_url(venue, alert.base)}]
        )
    return text, keyboard
