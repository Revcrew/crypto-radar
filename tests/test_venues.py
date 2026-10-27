"""Tests for venue clients: payload parsing, filters, normalization."""
import unittest

from radar.venues import build_venues
from radar.venues.binance import BinanceClient
from radar.venues.bingx import BingxClient
from radar.venues.bybit import BybitClient
from radar.venues.hyperliquid import HyperliquidClient
from radar.venues.kucoin import KucoinClient
from radar.venues.mexc import MexcClient
from radar.venues.okx import OkxClient


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.proxies = {}

    def get(self, *args, **kwargs):
        return FakeResponse(self._payload)

    def post(self, *args, **kwargs):
        return FakeResponse(self._payload)


def with_payload(client, payload):
    client._session = FakeSession(payload)
    return client.fetch()


class TestVenueParsing(unittest.TestCase):
    def test_mexc(self):
        quotes = with_payload(MexcClient(), {
            "success": True, "code": 0,
            "data": [
                {"symbol": "BTC_USDT", "lastPrice": "67000.5", "amount24": "1500000"},
                {"symbol": "NOPE", "lastPrice": "1", "amount24": "1"},
                {"symbol": "ETH_USDT", "lastPrice": "bad", "amount24": "1"},
            ],
        })
        self.assertEqual(len(quotes), 1)
        q = quotes[0]
        self.assertEqual((q.venue, q.base, q.price, q.turnover), ("mexc", "BTC", 67000.5, 1500000.0))
        self.assertFalse(q.turnover_native)

    def test_bingx_native_volume(self):
        quotes = with_payload(BingxClient(), {
            "code": 0,
            "data": [{"symbol": "BTC-USDT", "lastPrice": "67000", "volume": "25000"}],
        })
        self.assertEqual(len(quotes), 1)
        q = quotes[0]
        self.assertEqual(q.base, "BTC")
        self.assertEqual(q.turnover, 25000.0)
        self.assertTrue(q.turnover_native)

    def test_okx(self):
        quotes = with_payload(OkxClient(), {
            "code": "0",
            "data": [{"instId": "BTC-USDT-SWAP", "last": "67000", "volCcy24h": "900000"}],
        })
        self.assertEqual(len(quotes), 1)
        self.assertEqual((quotes[0].base, quotes[0].turnover), ("BTC", 900000.0))

    def test_okx_error_code_raises(self):
        client = OkxClient()
        client._session = FakeSession({"code": "12345", "data": []})
        with self.assertRaises(ValueError):
            client.fetch()

    def test_hyperliquid(self):
        quotes = with_payload(HyperliquidClient(), [
            {"universe": [{"name": "BTC"}, {"name": "ETH"}]},
            [{"markPx": "67000", "dayNtlVlm": "5000000"},
             {"markPx": "3500", "dayNtlVlm": "1000000"}],
        ])
        by_base = {q.base: q for q in quotes}
        self.assertEqual(by_base["BTC"].turnover, 5000000.0)
        self.assertEqual(by_base["ETH"].price, 3500.0)

    def test_binance_filters(self):
        quotes = with_payload(BinanceClient(), [
            {"symbol": "BTCUSDT", "lastPrice": "67000", "quoteVolume": "800000"},
            {"symbol": "BTCUPUSDT", "lastPrice": "10", "quoteVolume": "999999999"},
            {"symbol": "BTCUSD_240927", "lastPrice": "67000", "quoteVolume": "1"},
        ])
        self.assertEqual([q.base for q in quotes], ["BTC"])
        self.assertEqual(quotes[0].turnover, 800000.0)

    def test_bybit(self):
        quotes = with_payload(BybitClient(), {
            "retCode": 0,
            "result": {"list": [
                {"symbol": "BTCUSDT", "lastPrice": "67000", "turnover24h": "700000"},
            ]},
        })
        self.assertEqual((quotes[0].base, quotes[0].turnover), ("BTC", 700000.0))

    def test_kucoin_xbt_normalized(self):
        quotes = with_payload(KucoinClient(), {
            "code": "200000",
            "data": [
                {"symbol": "XBTUSDTM", "lastTradePrice": 67000, "turnoverOf24h": 600000},
                {"symbol": "ETHUSDTM", "lastTradePrice": 3500, "turnoverOf24h": 500000},
            ],
        })
        by_base = {q.base: q for q in quotes}
        self.assertIn("BTC", by_base)  # XBT -> BTC
        self.assertNotIn("XBT", by_base)
        self.assertEqual(by_base["BTC"].price, 67000.0)

    def test_proxy_flags(self):
        venues = {v.name: v for v in build_venues()}
        for name in ("binance", "bybit", "kucoin"):
            self.assertTrue(venues[name].uses_proxy, name)
        for name in ("mexc", "bingx", "okx", "hyperliquid"):
            self.assertFalse(venues[name].uses_proxy, name)

    def test_canonical_order(self):
        self.assertEqual(
            [v.name for v in build_venues()],
            ["mexc", "kucoin", "bingx", "binance", "bybit", "okx", "hyperliquid"],
        )


if __name__ == "__main__":
    unittest.main()
