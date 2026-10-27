"""Sample-collection checkpoint (TZ 3).

Every 4 hours the CEX alert counter is checked. Until 500 alerts: silence
(a log line only). On reaching 500: run the frozen-methodology analysis,
write ANALYSIS-500.md next to the code, send a one-off Telegram summary,
then disable the check permanently (flag survives restarts).
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("radar.checkpoint")

TARGET_N = 500
CHECK_INTERVAL_S = 4 * 3600


class Checkpoint:
    def __init__(self, journal, state, sender, service_dir: str,
                 dry_run: bool = False, target: int = TARGET_N):
        self._journal = journal
        self._state = state
        self._sender = sender
        self._service_dir = service_dir
        self._dry_run = dry_run
        self._target = target

    def check(self, now: float) -> str:
        """Return 'waiting' | 'triggered' | 'already'."""
        section = self._state.get_section("checkpoint")
        if section.get("triggered"):
            return "already"
        n = self._journal.count("alerts")
        if n < self._target:
            log.info("checkpoint: %d/%d CEX alerts collected — waiting", n, self._target)
            return "waiting"
        log.info("checkpoint: target reached (%d alerts) — running analysis", n)
        from .analysis import run_analysis

        report = run_analysis(self._journal.db_path)
        report_path = os.path.join(self._service_dir, "ANALYSIS-500.md")
        report.write_markdown(report_path)
        log.info("checkpoint: analysis written to %s", report_path)

        summary = report.telegram_summary()
        if self._dry_run:
            log.info("DRY-RUN checkpoint report (not sent):\n%s", summary)
        else:
            results = self._sender.send_alert(summary, [])
            failed = [c for c, ok in results.items() if not ok]
            if failed:
                log.error("checkpoint report failed for %s; no fallback", failed)
            else:
                log.info("checkpoint: one-off report sent")

        section["triggered"] = True
        section["triggered_at"] = now
        section["n"] = n
        self._state.save()
        return "triggered"
