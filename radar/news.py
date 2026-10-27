"""Crypto-crime news monitor (TZ 1.6).

RSS: CoinDesk + Cointelegraph, poll 5 min. Keyword match on the headline;
dedup by headline hash, persistent. First poll after start only seeds.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

from .state import StateStore

log = logging.getLogger("radar.news")

FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
}

KEYWORDS = (
    "hack",
    "hacked",
    "exploit",
    "delist",
    "delisting",
    "halt withdrawals",
    "suspends withdrawals",
    "sec charges",
    "rug",
    "insolvency",
    "bankrupt",
)

_MAX_SEEN = 5000


def _compile_pattern() -> re.Pattern:
    parts = []
    for kw in KEYWORDS:
        words = kw.split()
        # allow "halts withdrawals" for "halt withdrawals" etc.
        first = re.escape(words[0]) + r"s?"
        rest = [re.escape(w) for w in words[1:]]
        # word boundaries so "rug" doesn't match "shrugged"/"drugged"
        parts.append(r"\b" + r"\s+".join([first] + rest) + r"\b")
    return re.compile("|".join(parts), re.IGNORECASE)


_PATTERN = _compile_pattern()


def keyword_hit(title: str) -> bool:
    return _PATTERN.search(title) is not None


@dataclass(frozen=True)
class NewsAlert:
    source: str
    title: str
    url: str
    ts: float


class NewsMonitor:
    def __init__(self, state: StateStore, timeout: int = 20):
        self._state = state
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "crypto-crime-radar/0.1"})
        self._timeout = timeout

    def poll(self, now: float) -> list[NewsAlert]:
        items: list[tuple[str, str, str]] = []  # (source, title, link)
        for source, url in FEEDS.items():
            try:
                items.extend((source, t, link) for t, link in self._fetch_feed(url))
            except Exception as exc:
                log.warning("news feed %s failed: %s", source, type(exc).__name__)

        section = self._state.get_section("news")
        seen = set(section.setdefault("seen", []))
        if not section.get("seeded"):
            section["seeded"] = True
            section["seen"] = sorted(_hash(t) for _s, t, _l in items)
            self._state.save()
            log.info("news: seeded %d headlines, no alerts on first poll", len(items))
            return []

        alerts: list[NewsAlert] = []
        for source, title, link in items:
            if not keyword_hit(title):
                continue
            digest = _hash(title)
            if digest in seen:
                continue
            seen.add(digest)
            alerts.append(NewsAlert(source, title, link, now))
        section["seen"] = sorted(seen)[-_MAX_SEEN:]
        self._state.save()
        return alerts

    def _fetch_feed(self, url: str) -> list[tuple[str, str]]:
        resp = self._session.get(url, timeout=self._timeout)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items: list[tuple[str, str]] = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if title and link:
                items.append((title, link))
        return items


def _hash(title: str) -> str:
    return hashlib.sha1(title.encode("utf-8")).hexdigest()


def format_news_alert(alert: NewsAlert) -> tuple[str, list[list[dict]]]:
    text = f"📰 <b>{html.escape(alert.source)}</b>\n{html.escape(alert.title)}"
    keyboard = [[{"text": "Читать", "url": alert.url}]]
    return text, keyboard
