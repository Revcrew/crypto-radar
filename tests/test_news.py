"""Tests for radar/news.py: keyword matching, dedup, seeding."""
import os
import tempfile
import unittest

from radar.news import NewsMonitor, format_news_alert, keyword_hit
from radar.state import StateStore


class TestKeywordHit(unittest.TestCase):
    def test_hits(self):
        for title in [
            "Exchange hacked, $40M drained",
            "SEC charges founder with fraud",
            "DEX halts withdrawals amid probe",
            "Bridge exploit confirmed by team",
            "Token delisting next Friday",
            "Rug pull alert: liquidity removed",
            "Lender faces insolvency rumors",
            "Bankrupt fund's wallets move",
        ]:
            self.assertTrue(keyword_hit(title), title)

    def test_no_false_positives(self):
        for title in [
            "Bitcoin shrugs off macro concerns",
            "Drug enforcement seizes crypto",
            "Analysts bullish on Ethereum upgrade",
            "How to debug smart contracts",
        ]:
            self.assertFalse(keyword_hit(title), title)

    def test_case_insensitive(self):
        self.assertTrue(keyword_hit("MAJOR HACK HITS EXCHANGE"))


class FakeNewsMonitor(NewsMonitor):
    """Same item list for both feeds on each poll (also tests cross-feed dedup)."""

    def __init__(self, state, polls):
        super().__init__(state)
        self._polls = list(polls)
        self._current: list = []

    def poll(self, now):
        self._current = self._polls.pop(0) if self._polls else []
        return super().poll(now)

    def _fetch_feed(self, url):
        return self._current


class TestNewsMonitor(unittest.TestCase):
    def _monitor(self, rounds):
        tmpdir = tempfile.mkdtemp()
        state = StateStore(os.path.join(tmpdir, "state.json"))
        return FakeNewsMonitor(state, rounds)

    def test_first_poll_only_seeds(self):
        mon = self._monitor([[("Huge hack drains exchange", "https://x/1")]])
        self.assertEqual(mon.poll(now=0.0), [])

    def test_alert_on_keyword(self):
        mon = self._monitor([
            [("Market update", "https://x/0")],
            [("Market update", "https://x/0"),
             ("Exchange hacked, funds safe-ish", "https://x/1")],
        ])
        mon.poll(now=0.0)
        alerts = mon.poll(now=300.0)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].source, "CoinDesk")
        self.assertIn("hacked", alerts[0].title)

    def test_dedup(self):
        mon = self._monitor([
            [],
            [("Bridge exploit confirmed", "https://x/1")],
            [("Bridge exploit confirmed", "https://x/1")],
        ])
        mon.poll(now=0.0)
        self.assertEqual(len(mon.poll(now=300.0)), 1)
        self.assertEqual(mon.poll(now=600.0), [])

    def test_format(self):
        from radar.news import NewsAlert
        text, keyboard = format_news_alert(
            NewsAlert("CoinDesk", "Hack <b>x</b>", "https://x/1", 0.0)
        )
        self.assertIn("📰", text)
        self.assertIn("CoinDesk", text)
        self.assertIn("Hack &lt;b&gt;x&lt;/b&gt;", text)  # escaped
        self.assertEqual(keyboard, [[{"text": "Читать", "url": "https://x/1"}]])


if __name__ == "__main__":
    unittest.main()
