"""Tests for radar/dex.py: triggers, filters, cooldown, seeding, formatting."""
import os
import tempfile
import unittest

from radar.dex import (
    COOLDOWN_S,
    DexAlert,
    DexMonitor,
    DexPair,
    DexScreenerClient,
    format_dex_alert,
)
from radar.state import StateStore


def pair(chain="solana", pair_addr="pair1", price=1.0, liquidity=100_000.0,
         symbol="TOKYO", token_addr="tok1"):
    return DexPair(
        chain=chain, pair_address=pair_addr, token_address=token_addr,
        token_symbol=symbol, token_name="Tokyo Token",
        price_usd=price, liquidity_usd=liquidity,
        url="https://dexscreener.com/solana/pair1",
    )


class FakeClient:
    """Returns canned pair lists per poll call."""

    def __init__(self, rounds):
        self._rounds = list(rounds)

    def fetch_pairs(self):
        return self._rounds.pop(0) if self._rounds else []


def fresh_monitor(rounds):
    tmpdir = tempfile.mkdtemp()
    state = StateStore(os.path.join(tmpdir, "state.json"))
    return DexMonitor(state, client=FakeClient(rounds)), state


class TestDexMonitor(unittest.TestCase):
    def test_first_poll_only_seeds(self):
        mon, state = fresh_monitor([[pair(price=2.0)]])  # +100% vs nothing
        self.assertEqual(mon.poll(now=0.0), [])
        self.assertTrue(state.get_section("dex")["seeded"])

    def test_trigger_5min(self):
        mon, _ = fresh_monitor([
            [pair(price=1.0)],
            [pair(price=1.5)],  # +50% over 5 min
        ])
        mon.poll(now=0.0)  # seed
        alerts = mon.poll(now=300.0)
        self.assertEqual(len(alerts), 1)
        a = alerts[0]
        self.assertEqual(a.window_s, 300)
        self.assertAlmostEqual(a.pct, 50.0)
        self.assertEqual(a.direction, "pump")

    def test_trigger_1h(self):
        mon, _ = fresh_monitor([
            [pair(price=1.0)],
            [pair(price=1.0)],
            [pair(price=1.9)],  # +90% over 1 h
        ])
        mon.poll(now=0.0)
        mon.poll(now=1800.0)
        alerts = mon.poll(now=3600.0)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].window_s, 3600)

    def test_below_threshold_no_alert(self):
        mon, _ = fresh_monitor([
            [pair(price=1.0)],
            [pair(price=1.3)],  # +30% < 40%
        ])
        mon.poll(now=0.0)
        self.assertEqual(mon.poll(now=300.0), [])

    def test_liquidity_filter(self):
        mon, _ = fresh_monitor([
            [pair(price=1.0, liquidity=10_000.0)],
            [pair(price=2.0, liquidity=10_000.0)],  # +100% but illiquid
        ])
        mon.poll(now=0.0)
        self.assertEqual(mon.poll(now=300.0), [])

    def test_cooldown_per_pool(self):
        self.assertEqual(COOLDOWN_S, 3600)
        mon, _ = fresh_monitor([
            [pair(price=1.0)],
            [pair(price=1.5)],
            [pair(price=2.0)],  # still moving, but cooldown
            [pair(price=4.0)],  # after cooldown: +100% vs 1h ref (2.0)
        ])
        mon.poll(now=0.0)
        self.assertEqual(len(mon.poll(now=300.0)), 1)
        self.assertEqual(mon.poll(now=600.0), [])
        self.assertEqual(len(mon.poll(now=4200.0)), 1)

    def test_one_alert_per_token_picks_max(self):
        mon, _ = fresh_monitor([
            [pair(pair_addr="p1", price=1.0), pair(pair_addr="p2", price=1.0)],
            [pair(pair_addr="p1", price=1.5), pair(pair_addr="p2", price=2.2)],
        ])
        mon.poll(now=0.0)
        alerts = mon.poll(now=300.0)
        self.assertEqual(len(alerts), 1)
        self.assertAlmostEqual(alerts[0].pct, 120.0)

    def test_dump_direction(self):
        mon, _ = fresh_monitor([
            [pair(price=1.0)],
            [pair(price=0.5)],  # -50%
        ])
        mon.poll(now=0.0)
        alerts = mon.poll(now=300.0)
        self.assertEqual(alerts[0].direction, "dump")

    def test_parse_pair(self):
        raw = {
            "pairAddress": "abc",
            "url": "https://dexscreener.com/solana/abc",
            "baseToken": {"address": "tok", "name": "N", "symbol": "SYM"},
            "priceUsd": "0.5",
            "liquidity": {"usd": 60000},
        }
        p = DexScreenerClient._parse_pair("solana", raw)
        self.assertEqual((p.token_symbol, p.price_usd, p.liquidity_usd),
                         ("SYM", 0.5, 60000.0))
        bad = dict(raw, priceUsd="0")
        self.assertIsNone(DexScreenerClient._parse_pair("solana", bad))

    def test_format(self):
        alert = DexAlert(
            chain="solana", token_symbol="TOKYO", token_name="Tokyo",
            contract="AbC123456789x9Zz", pool_address="p1",
            direction="pump", pct=125.4,
            window_s=300, price_usd=0.001234, liquidity_usd=125_300.0,
            url="https://dexscreener.com/solana/p1", ts=0.0,
        )
        text, keyboard = format_dex_alert(alert)
        self.assertIn("🟢", text)
        self.assertIn("DEX-ПАМП TOKYO", text)
        self.assertIn("+125.40%", text)
        self.assertIn("5 мин", text)
        self.assertIn("Solana", text)
        self.assertIn("$125.3K", text)
        self.assertIn("AbC123…x9Zz", text)
        self.assertEqual(keyboard, [[{"text": "DexScreener",
                                     "url": "https://dexscreener.com/solana/p1"}]])


if __name__ == "__main__":
    unittest.main()
