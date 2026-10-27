"""Tests for radar/proxy.py and radar/telegram.py."""
import unittest

from radar.proxy import ProxyRouter, load_proxy_url
from radar.telegram import TelegramSender


class FakeClock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


class TestProxyRouter(unittest.TestCase):
    def test_pause_after_two_errors(self):
        clock = FakeClock()
        r = ProxyRouter("http://u:p@h:1", time_fn=clock)
        self.assertFalse(r.is_paused("binance"))
        r.record_error("binance")
        self.assertFalse(r.is_paused("binance"))
        r.record_error("binance")
        self.assertTrue(r.is_paused("binance"))
        clock.now += 121
        self.assertFalse(r.is_paused("binance"))

    def test_success_resets_counter(self):
        clock = FakeClock()
        r = ProxyRouter("http://u:p@h:1", time_fn=clock)
        r.record_error("bybit")
        r.record_success("bybit")
        r.record_error("bybit")
        self.assertFalse(r.is_paused("bybit"))

    def test_direct_venues_never_pause(self):
        r = ProxyRouter("http://u:p@h:1")
        for _ in range(5):
            r.record_error("mexc")
        self.assertFalse(r.is_paused("mexc"))

    def test_proxies_dict(self):
        r = ProxyRouter("http://u:p@h:1")
        self.assertEqual(
            r.proxies, {"http": "http://u:p@h:1", "https": "http://u:p@h:1"}
        )
        self.assertIsNone(ProxyRouter(None).proxies)

    def test_load_proxy_url_missing(self):
        self.assertIsNone(load_proxy_url("/nonexistent-dir-xyz"))


class FakePostResponse:
    def __init__(self, ok=True):
        self._ok = ok

    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": self._ok, "description": "boom" if not self._ok else None}


class FakeSession:
    def __init__(self, ok=True, fail=False):
        self._ok = ok
        self._fail = fail
        self.last_payload = None

    def post(self, url, json=None, timeout=None):
        self.last_payload = json
        if self._fail:
            raise ConnectionError("down")
        return FakePostResponse(self._ok)


class TestTelegramSender(unittest.TestCase):
    def test_payload_shape(self):
        session = FakeSession()
        sender = TelegramSender("tok", ("111",), session=session)
        results = sender.send_alert("<b>hi</b>", [[{"text": "x", "url": "https://e.com"}]])
        self.assertEqual(results, {"111": True})
        payload = session.last_payload
        self.assertEqual(payload["chat_id"], "111")
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertTrue(payload["disable_web_page_preview"])
        self.assertEqual(
            payload["reply_markup"]["inline_keyboard"],
            [[{"text": "x", "url": "https://e.com"}]],
        )

    def test_transport_error_no_fallback(self):
        sender = TelegramSender("tok", ("111", "222"), session=FakeSession(fail=True))
        results = sender.send_alert("t", [])
        self.assertEqual(results, {"111": False, "222": False})

    def test_api_error_no_fallback(self):
        sender = TelegramSender("tok", ("111",), session=FakeSession(ok=False))
        self.assertEqual(sender.send_alert("t", []), {"111": False})


if __name__ == "__main__":
    unittest.main()
