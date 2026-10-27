"""Main poll loop: fetch all venues (~5 s cycle) -> detect -> alert.

A venue that errors (geo-block, timeout, bad payload) is skipped for the
cycle; the service never dies because of one venue.
"""
from __future__ import annotations

import logging
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .alerts import AlertAggregator, format_alert
from .checkpoint import CHECK_INTERVAL_S, Checkpoint
from .config import Config
from .detector import Detector
from .dex import DexMonitor, format_dex_alert
from .journal import Journal
from .news import NewsMonitor, format_news_alert
from .proxy import ProxyRouter, load_proxy_url
from .state import StateStore
from .telegram import TelegramSender
from .venues import CANONICAL_ORDER, build_venues

log = logging.getLogger("radar.engine")

CYCLE_S = 5.0
DEX_POLL_S = 60.0
NEWS_POLL_S = 300.0


class Engine:
    def __init__(self, config: Config, dry_run: bool = False):
        self._config = config
        self._dry_run = dry_run
        self._proxy_url = load_proxy_url(config.service_dir)
        self._proxy_router = ProxyRouter(self._proxy_url)
        self._venues = build_venues(proxies=self._proxy_router.proxies)
        self._detector = Detector()
        self._aggregator = AlertAggregator()
        self._sender = TelegramSender(config.bot_token, config.chat_ids)
        self._state = StateStore(
            os.path.join(config.service_dir, "state", "state.json")
        )
        self._dex = DexMonitor(self._state)
        self._news = NewsMonitor(self._state)
        self._journal = Journal(
            os.path.join(config.service_dir, "journal.sqlite3"), config.cohort
        )
        self._checkpoint = Checkpoint(
            self._journal, self._state, self._sender,
            config.service_dir, dry_run=dry_run,
        )
        self._last_checkpoint = 0.0
        self._last_dex = 0.0
        self._last_news = 0.0
        self._stop = False
        if self._proxy_url:
            log.info(
                "proxy configured for binance/bybit/kucoin (host hidden); "
                "others go direct"
            )
        else:
            log.warning("no proxy.env: binance/bybit/kucoin will likely be geo-blocked")

    # -- lifecycle ---------------------------------------------------------

    def run(self, once: bool = False) -> None:
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)
        log.info(
            "engine starting: %d venues, cycle %.0fs, dry_run=%s",
            len(self._venues),
            CYCLE_S,
            self._dry_run,
        )
        while not self._stop:
            started = time.time()
            try:
                self.run_cycle()
                self._run_due_monitors()
            except Exception:  # never let one cycle kill the service
                log.exception("cycle crashed; continuing")
            if once:
                break
            time.sleep(max(0.0, CYCLE_S - (time.time() - started)))
        log.info("engine stopped")

    def _handle_stop(self, *_args) -> None:
        self._stop = True

    # -- one cycle -----------------------------------------------------------

    def run_cycle(self) -> None:
        now = time.time()
        cycle_start = now
        fetched = self._fetch_all()
        quotes: list = []
        for venue_name in CANONICAL_ORDER:
            venue_quotes, _err = fetched[venue_name]
            quotes.extend(venue_quotes)

        self._detector.update(quotes, now)
        self._journal.on_cex_quotes(quotes, now)
        triggers = self._detector.detect(now)
        alerts = self._aggregator.aggregate(triggers, self._detector, now)
        for alert in alerts:
            self._deliver(alert)
        self._log_summary(fetched, alerts, time.time() - cycle_start)

    def _fetch_all(self) -> dict[str, tuple[list, str | None]]:
        results: dict[str, tuple[list, str | None]] = {}
        with ThreadPoolExecutor(max_workers=len(self._venues)) as pool:
            futures = {pool.submit(self._fetch_one, v): v for v in self._venues}
            for fut in as_completed(futures):
                venue = futures[fut]
                results[venue.name] = fut.result()
        return results

    def _fetch_one(self, venue) -> tuple[list, str | None]:
        if self._proxy_router.is_paused(venue.name):
            return [], "PAUSED"
        try:
            quotes = venue.fetch()
        except Exception as exc:
            self._proxy_router.record_error(venue.name)
            return [], f"ERR:{type(exc).__name__}"
        self._proxy_router.record_success(venue.name)
        return quotes, None

    # -- scheduled monitors (dex 60s, news 300s) -------------------------------

    def _run_due_monitors(self) -> None:
        now = time.time()
        if now - self._last_dex >= DEX_POLL_S:
            self._last_dex = now
            dex_alerts = self._safe_poll(self._dex, "dex")
            self._journal.on_dex_pairs(self._dex.last_pairs, now)
            for alert in dex_alerts:
                self._deliver_monitor_alert(
                    alert,
                    format_dex_alert,
                    self._journal.record_dex_alert,
                    f"dex {alert.token_symbol}",
                )
        if now - self._last_news >= NEWS_POLL_S:
            self._last_news = now
            for alert in self._safe_poll(self._news, "news"):
                self._deliver_monitor_alert(
                    alert,
                    format_news_alert,
                    self._journal.record_news_alert,
                    f"news {alert.source}",
                )
        if now - self._last_checkpoint >= CHECK_INTERVAL_S:
            self._last_checkpoint = now
            try:
                self._checkpoint.check(now)
            except Exception:
                log.exception("checkpoint check failed")

    @staticmethod
    def _safe_poll(monitor, name: str) -> list:
        try:
            return monitor.poll(time.time())
        except Exception:
            log.exception("%s monitor failed; skipping this round", name)
            return []

    def _deliver_monitor_alert(self, alert, fmt, record_fn, label: str) -> None:
        text, keyboard = fmt(alert)
        if self._dry_run:
            log.info("DRY-RUN %s alert (not sent, not journaled):\n%s", label, text)
            return
        results = self._sender.send_alert(text, keyboard)
        if all(results.values()):
            log.info("%s alert sent", label)
            try:
                record_fn(alert)
            except Exception:
                log.exception("journal record failed")
        else:
            failed = [c for c, ok in results.items() if not ok]
            log.error("%s alert send failed for chats %s; no fallback", label, failed)

    # -- delivery & logging ----------------------------------------------------

    def _deliver(self, alert) -> None:
        text, keyboard = format_alert(alert)
        if self._dry_run:
            log.info("DRY-RUN alert (not sent, not journaled):\n%s", text)
            return
        results = self._sender.send_alert(text, keyboard)
        if all(results.values()):
            log.info("alert sent: %s %s (%+.2f%%)", alert.base, alert.direction, alert.pct)
            try:
                # journal entry is created at the moment of successful delivery
                self._journal.record_alert(alert, self._config.cohort)
            except Exception:
                log.exception("journal record failed")
        else:
            failed = [c for c, ok in results.items() if not ok]
            log.error("alert send failed for chats %s; no fallback attempted", failed)

    def _log_summary(self, fetched: dict, alerts: list, elapsed: float) -> None:
        parts = []
        for venue_name in CANONICAL_ORDER:
            quotes, err = fetched[venue_name]
            if err:
                parts.append(f"{venue_name}={err}")
            else:
                parts.append(f"{venue_name}=OK({len(quotes)})")
        log.info(
            "cycle %.1fs | alerts=%d | %s",
            elapsed,
            len(alerts),
            " ".join(parts),
        )
