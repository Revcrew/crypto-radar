"""Tests for radar/alerts.py: aggregation, cooldown, formatting, buttons."""
import unittest

from radar.alerts import (
    COOLDOWN_S,
    AlertAggregator,
    format_alert,
    venue_url,
)
from radar.detector import Detector, Trigger
from radar.venues.base import Quote


def q(venue, base, price, turnover=1_000_000):
    return Quote(venue=venue, symbol=f"{base}/USDT", base=base,
                 price=price, turnover=turnover)


def feed(detector, venue, base, prices, step=60.0, start=0.0):
    for i, p in enumerate(prices):
        detector.update([q(venue, base, p)], now=start + i * step)


class TestAggregation(unittest.TestCase):
    def test_origin_is_max_abs_pct(self):
        d = Detector()
        feed(d, "mexc", "BTC", [100.0, 106.0])       # +6% 1min
        feed(d, "binance", "BTC", [100.0, 91.0])      # -9% 1min
        feed(d, "okx", "BTC", [100.0, 100.0])
        triggers = [
            Trigger("mexc", "BTC", 6.0, 60, 106.0, 100.0, 1_000_000),
            Trigger("binance", "BTC", -9.0, 60, 91.0, 100.0, 1_000_000),
        ]
        alerts = AlertAggregator().aggregate(triggers, d, now=60.0)
        self.assertEqual(len(alerts), 1)  # one asset = one alert
        a = alerts[0]
        self.assertEqual(a.origin, "binance")
        self.assertEqual(a.direction, "dump")
        self.assertAlmostEqual(a.pct, -9.0)

    def test_cooldown(self):
        self.assertEqual(COOLDOWN_S, 1800)
        d = Detector()
        feed(d, "mexc", "BTC", [100.0, 106.0])
        agg = AlertAggregator()
        triggers = [Trigger("mexc", "BTC", 6.0, 60, 106.0, 100.0, 1_000_000)]
        self.assertEqual(len(agg.aggregate(triggers, d, now=60.0)), 1)
        self.assertEqual(len(agg.aggregate(triggers, d, now=120.0)), 0)  # cooldown
        self.assertEqual(len(agg.aggregate(triggers, d, now=1900.0)), 1)  # expired

    def test_button_order_origin_first_then_canonical(self):
        d = Detector()
        feed(d, "okx", "BTC", [100.0, 112.0])
        feed(d, "mexc", "BTC", [100.0, 100.0])
        feed(d, "binance", "BTC", [100.0, 100.0])
        # kucoin does NOT trade BTC here -> excluded from buttons
        triggers = [Trigger("okx", "BTC", 12.0, 60, 112.0, 100.0, 1_000_000)]
        alerts = AlertAggregator().aggregate(triggers, d, now=60.0)
        text, keyboard = format_alert(alerts[0])
        labels = [row[0]["text"] for row in keyboard]
        self.assertEqual(labels, ["📍 OKX", "MEXC", "Binance"])
        self.assertTrue(keyboard[0][0]["url"].startswith("https://www.okx.com/trade-swap/btc-usdt-swap"))

    def test_format_contents(self):
        d = Detector()
        feed(d, "mexc", "BTC", [100.0, 106.0])
        triggers = [Trigger("mexc", "BTC", 6.0, 60, 106.0, 100.0, 2_500_000)]
        alerts = AlertAggregator().aggregate(triggers, d, now=60.0)
        text, _kb = format_alert(alerts[0])
        self.assertIn("🟢", text)
        self.assertIn("ПАМП BTC", text)
        self.assertIn("+6.00%", text)
        self.assertIn("1 мин", text)
        self.assertIn("MEXC", text)
        self.assertIn("$2.5M", text)

    def test_venue_urls(self):
        self.assertEqual(venue_url("mexc", "BTC"), "https://www.mexc.com/futures/BTC_USDT")
        self.assertEqual(venue_url("kucoin", "BTC"), "https://www.kucoin.com/futures/trade/XBTUSDTM")
        self.assertEqual(venue_url("kucoin", "ETH"), "https://www.kucoin.com/futures/trade/ETHUSDTM")
        self.assertEqual(venue_url("bingx", "ETH"), "https://bingx.com/en/perpetual/ETH-USDT")
        self.assertEqual(venue_url("binance", "SOL"), "https://www.binance.com/en/futures/SOLUSDT")
        self.assertEqual(venue_url("bybit", "SOL"), "https://www.bybit.com/trade/usdt/SOLUSDT")
        self.assertEqual(venue_url("okx", "SOL"), "https://www.okx.com/trade-swap/sol-usdt-swap")
        self.assertEqual(venue_url("hyperliquid", "HYPE"), "https://app.hyperliquid.xyz/trade/HYPE")


if __name__ == "__main__":
    unittest.main()
