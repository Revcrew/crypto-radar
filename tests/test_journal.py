"""Tests for radar/journal.py: recording, honest fills, extrema, migration, CSV."""
import csv
import os
import sqlite3
import tempfile
import unittest

from radar.alerts import Alert
from radar.dex import DexAlert
from radar.journal import HORIZONS, Journal
from radar.news import NewsAlert
from radar.venues.base import Quote


def cex_alert(ts=1000.0, base="BTC", origin="mexc", price=100.0):
    return Alert(
        base=base, direction="pump", pct=6.0, window_s=60, origin=origin,
        price_before=price, price_after=price, turnover=1_000_000,
        turnover_native=False, others=(), venues_traded=(), ts=ts,
    )


def dex_alert(ts=1000.0):
    return DexAlert(
        chain="solana", token_symbol="TOKYO", token_name="Tokyo",
        contract="tok1", pool_address="pair1", direction="pump", pct=50.0,
        window_s=300, price_usd=1.0, liquidity_usd=100_000.0,
        url="https://dexscreener.com/solana/pair1", ts=ts,
    )


def q(venue, base, price):
    return Quote(venue=venue, symbol=f"{base}/USDT", base=base,
                 price=price, turnover=1_000_000)


def fresh_journal():
    tmpdir = tempfile.mkdtemp()
    return Journal(os.path.join(tmpdir, "journal.sqlite3"), cohort="A"), tmpdir


class TestRecording(unittest.TestCase):
    def test_record_alert(self):
        j, _tmp = fresh_journal()
        row_id = j.record_alert(cex_alert())
        self.assertEqual(row_id, 1)
        self.assertEqual(j.count("alerts"), 1)
        row = j._conn.execute("SELECT * FROM alerts").fetchone()
        self.assertEqual((row["asset"], row["origin"], row["cohort"]), ("BTC", "mexc", "A"))
        # extrema seeded with alert price
        self.assertEqual((row["min60"], row["max60"]), (100.0, 100.0))
        self.assertIsNone(row["pe30"])

    def test_record_dex_and_news(self):
        j, _tmp = fresh_journal()
        j.record_dex_alert(dex_alert())
        j.record_news_alert(NewsAlert("CoinDesk", "Hack!", "https://x/1", 1000.0))
        self.assertEqual(j.count("dex_alerts"), 1)
        self.assertEqual(j.count("news_alerts"), 1)
        row = j._conn.execute("SELECT * FROM dex_alerts").fetchone()
        self.assertEqual((row["network"], row["pool_ref"]), ("solana", "pair1"))


class TestHorizonFills(unittest.TestCase):
    def test_fills_inside_window(self):
        j, _tmp = fresh_journal()
        j.record_alert(cex_alert(ts=1000.0, price=100.0))
        # pe30 window [1030, 1210]; pe60/p1 window [1060, 1240]
        j.on_cex_quotes([q("mexc", "BTC", 110.0)], now=1100.0)
        row = j._conn.execute("SELECT * FROM alerts").fetchone()
        self.assertEqual(row["pe30"], 110.0)
        self.assertEqual(row["pe60"], 110.0)
        self.assertEqual(row["p1"], 110.0)
        self.assertIsNone(row["p5"])

    def test_missed_deadline_stays_null_forever(self):
        j, _tmp = fresh_journal()
        j.record_alert(cex_alert(ts=1000.0, price=100.0))
        # p10 window [1600, 1780]; tick at 2000 -> missed
        j.on_cex_quotes([q("mexc", "BTC", 120.0)], now=2000.0)
        row = j._conn.execute("SELECT * FROM alerts").fetchone()
        self.assertIsNone(row["p10"])
        # later ticks must NOT backfill
        j.on_cex_quotes([q("mexc", "BTC", 130.0)], now=5000.0)
        row = j._conn.execute("SELECT * FROM alerts").fetchone()
        self.assertIsNone(row["p10"])

    def test_too_early_not_filled(self):
        j, _tmp = fresh_journal()
        j.record_alert(cex_alert(ts=1000.0, price=100.0))
        j.on_cex_quotes([q("mexc", "BTC", 110.0)], now=1010.0)  # before pe30 deadline
        row = j._conn.execute("SELECT * FROM alerts").fetchone()
        self.assertIsNone(row["pe30"])

    def test_late_horizon_grace_5pct(self):
        j, _tmp = fresh_journal()
        j.record_alert(cex_alert(ts=0.0, price=100.0))
        # p1440: deadline 86400, grace max(180, 4320) = 4320 -> fill at 90000 ok
        j.on_cex_quotes([q("mexc", "BTC", 150.0)], now=90000.0)
        row = j._conn.execute("SELECT * FROM alerts").fetchone()
        self.assertEqual(row["p1440"], 150.0)
        # but at 91000 -> missed
        j2, _tmp2 = fresh_journal()
        j2.record_alert(cex_alert(ts=0.0, price=100.0))
        j2.on_cex_quotes([q("mexc", "BTC", 150.0)], now=91000.0)
        row = j2._conn.execute("SELECT * FROM alerts").fetchone()
        self.assertIsNone(row["p1440"])

    def test_wrong_venue_ignored(self):
        j, _tmp = fresh_journal()
        j.record_alert(cex_alert(ts=1000.0, origin="mexc"))
        j.on_cex_quotes([q("binance", "BTC", 110.0)], now=1100.0)  # not origin
        row = j._conn.execute("SELECT * FROM alerts").fetchone()
        self.assertIsNone(row["pe30"])


class TestExtrema(unittest.TestCase):
    def test_minmax_tracked(self):
        j, _tmp = fresh_journal()
        j.record_alert(cex_alert(ts=1000.0, price=100.0))
        j.on_cex_quotes([q("mexc", "BTC", 90.0)], now=1100.0)
        j.on_cex_quotes([q("mexc", "BTC", 120.0)], now=1200.0)
        row = j._conn.execute("SELECT * FROM alerts").fetchone()
        self.assertEqual((row["min60"], row["max60"]), (90.0, 120.0))
        self.assertEqual((row["min1440"], row["max1440"]), (90.0, 120.0))

    def test_min60_frozen_after_1h(self):
        j, _tmp = fresh_journal()
        j.record_alert(cex_alert(ts=0.0, price=100.0))
        j.on_cex_quotes([q("mexc", "BTC", 50.0)], now=5000.0)  # > 1h
        row = j._conn.execute("SELECT * FROM alerts").fetchone()
        self.assertEqual(row["min60"], 100.0)  # unchanged
        self.assertEqual(row["min1440"], 50.0)  # still tracked


class TestDexFollowup(unittest.TestCase):
    def _pair(self, chain, pair_addr, token_addr, price, liq):
        from radar.dex import DexPair
        return DexPair(chain, pair_addr, token_addr, "TOKYO", "Tokyo",
                       price, liq, "https://dexscreener.com")

    def test_exact_pool_used(self):
        j, _tmp = fresh_journal()
        j.record_dex_alert(dex_alert(ts=1000.0))
        pairs = [self._pair("solana", "pair1", "tok1", 1.5, 100_000.0)]
        j.on_dex_pairs(pairs, now=1100.0)
        row = j._conn.execute("SELECT * FROM dex_alerts").fetchone()
        self.assertEqual(row["pe30"], 1.5)

    def test_fallback_to_most_liquid_pool(self):
        j, _tmp = fresh_journal()
        j.record_dex_alert(dex_alert(ts=1000.0))  # pool pair1
        pairs = [
            self._pair("solana", "pair2", "tok1", 2.0, 50_000.0),
            self._pair("solana", "pair3", "tok1", 3.0, 200_000.0),
        ]
        j.on_dex_pairs(pairs, now=1100.0)
        row = j._conn.execute("SELECT * FROM dex_alerts").fetchone()
        self.assertEqual(row["pe30"], 3.0)  # most liquid fallback


class TestMigrationAndCsv(unittest.TestCase):
    def test_migration_adds_columns_without_loss(self):
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "journal.sqlite3")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE alerts (id INTEGER PRIMARY KEY, ts REAL, asset TEXT)")
        conn.execute("INSERT INTO alerts (ts, asset) VALUES (1.0, 'BTC')")
        conn.commit()
        conn.close()
        j = Journal(db_path)
        rows = j._conn.execute("SELECT * FROM alerts").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["asset"], "BTC")
        self.assertIn("pe30", rows[0].keys())

    def test_csv_exports(self):
        j, tmpdir = fresh_journal()
        j.record_alert(cex_alert(ts=1000.0))
        j.record_dex_alert(dex_alert())
        j.record_news_alert(NewsAlert("CoinDesk", "Hack!", "https://x/1", 1000.0))
        j.write_csvs()
        for name in ("journal.csv", "outcomes.csv", "dex_journal.csv", "news_journal.csv"):
            path = os.path.join(tmpdir, name)
            self.assertTrue(os.path.exists(path), name)
            with open(path, encoding="utf-8") as fh:
                reader = csv.reader(fh)
                header = next(reader)
                rows = list(reader)
            self.assertEqual(len(rows), 1, name)
        # outcomes.csv has horizon columns but not origin/cohort
        with open(os.path.join(tmpdir, "outcomes.csv"), encoding="utf-8") as fh:
            header = next(csv.reader(fh))
        self.assertIn("pe30", header)
        self.assertIn("p1440", header)
        self.assertNotIn("origin", header)


if __name__ == "__main__":
    unittest.main()
