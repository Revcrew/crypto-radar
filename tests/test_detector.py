"""Tests for radar/detector.py: triggers, thresholds, freshness."""
import unittest

from radar.detector import Detector, TRIGGER_RULES
from radar.venues.base import Quote


def q(venue, base, price, turnover=1_000_000, native=False):
    return Quote(
        venue=venue, symbol=f"{base}/USDT", base=base,
        price=price, turnover=turnover, turnover_native=native,
    )


class TestDetector(unittest.TestCase):
    def test_trigger_1min(self):
        d = Detector()
        d.update([q("mexc", "BTC", 100.0)], now=0.0)
        d.update([q("mexc", "BTC", 100.0)], now=30.0)
        d.update([q("mexc", "BTC", 106.0)], now=60.0)
        triggers = d.detect(now=60.0)
        self.assertEqual(len(triggers), 1)
        t = triggers[0]
        self.assertEqual((t.venue, t.base, t.window_s), ("mexc", "BTC", 60))
        self.assertAlmostEqual(t.pct, 6.0)

    def test_shortest_window_wins(self):
        # +15% over 15 min AND +15% in the last minute -> report 1-min window
        d = Detector()
        d.update([q("mexc", "BTC", 100.0)], now=0.0)
        d.update([q("mexc", "BTC", 100.0)], now=840.0)
        d.update([q("mexc", "BTC", 115.0)], now=900.0)
        triggers = d.detect(now=900.0)
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].window_s, 60)

    def test_trigger_15min_only(self):
        d = Detector()
        prices = [100.0 + i for i in range(14)]  # slow bleed to 113
        for i, p in enumerate(prices):
            d.update([q("mexc", "BTC", p)], now=float(i * 60))
        d.update([q("mexc", "BTC", 113.0)], now=900.0)
        triggers = d.detect(now=900.0)
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].window_s, 900)
        self.assertAlmostEqual(triggers[0].pct, 13.0)

    def test_below_threshold_no_trigger(self):
        d = Detector()
        d.update([q("mexc", "BTC", 100.0)], now=0.0)
        d.update([q("mexc", "BTC", 104.0)], now=60.0)  # +4% < 5%
        self.assertEqual(d.detect(now=60.0), [])

    def test_turnover_filter_blocks(self):
        d = Detector()
        d.update([q("mexc", "BTC", 100.0, turnover=100_000)], now=0.0)
        d.update([q("mexc", "BTC", 106.0, turnover=100_000)], now=60.0)
        self.assertEqual(d.detect(now=60.0), [])  # 100K < 500K for 1-min window

    def test_turnover_filter_5min_window(self):
        d = Detector()
        d.update([q("mexc", "BTC", 100.0, turnover=300_000)], now=0.0)
        d.update([q("mexc", "BTC", 110.0, turnover=300_000)], now=300.0)
        triggers = d.detect(now=300.0)
        self.assertEqual(len(triggers), 1)  # 300K > 200K ok for 5-min window
        self.assertEqual(triggers[0].window_s, 300)

    def test_bingx_native_thresholds(self):
        d = Detector()
        d.update([q("bingx", "BTC", 100.0, turnover=25_000, native=True)], now=0.0)
        d.update([q("bingx", "BTC", 106.0, turnover=25_000, native=True)], now=60.0)
        self.assertEqual(len(d.detect(now=60.0)), 1)  # 25K > 20K native

        d2 = Detector()
        d2.update([q("bingx", "BTC", 100.0, turnover=10_000, native=True)], now=0.0)
        d2.update([q("bingx", "BTC", 106.0, turnover=10_000, native=True)], now=60.0)
        self.assertEqual(d2.detect(now=60.0), [])  # 10K < 20K native

    def test_gap_in_history_skips_window(self):
        d = Detector()
        d.update([q("mexc", "BTC", 100.0)], now=0.0)
        d.update([q("mexc", "BTC", 120.0)], now=400.0)  # +20% but no ref sample
        self.assertEqual(d.detect(now=400.0), [])  # no phantom alert

    def test_stale_venue_skipped(self):
        d = Detector()
        d.update([q("mexc", "BTC", 100.0)], now=0.0)
        d.update([q("mexc", "BTC", 120.0)], now=10.0)
        self.assertEqual(d.detect(now=1000.0), [])  # latest sample is stale

    def test_unknown_turnover_fails_closed(self):
        d = Detector()
        d.update([q("mexc", "BTC", 100.0, turnover=None)], now=0.0)
        d.update([q("mexc", "BTC", 106.0, turnover=None)], now=60.0)
        self.assertEqual(d.detect(now=60.0), [])

    def test_trigger_rules_match_tz(self):
        by_window = {r.window_s: r for r in TRIGGER_RULES}
        self.assertEqual(by_window[60].pct, 5.0)
        self.assertEqual(by_window[300].pct, 8.0)
        self.assertEqual(by_window[900].pct, 12.0)
        self.assertEqual(by_window[60].min_turnover_usd, 500_000)
        self.assertEqual(by_window[300].min_turnover_usd, 200_000)
        self.assertEqual(by_window[60].min_turnover_native, 20_000)
        self.assertEqual(by_window[300].min_turnover_native, 5_000)


if __name__ == "__main__":
    unittest.main()
