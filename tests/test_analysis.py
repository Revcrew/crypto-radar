"""Tests for radar/analysis.py: PnL math, PASS bar, cuts, verdicts."""
import unittest

from radar.analysis import (
    HORIZON_COL,
    CellStats,
    _analyze_rows,
    _cell,
    _sign,
    _trades,
    run_analysis,
)


def make_row(ts, direction, entry_price, exit_map, origin="mexc",
             window_s=60, pct=-6.0, cohort="A"):
    row = {
        "ts": ts, "asset": "BTC", "origin": origin, "direction": direction,
        "pct": pct, "window_s": window_s, "price": entry_price, "cohort": cohort,
        "min60": entry_price * 0.99, "max60": entry_price * 1.04,
        "min1440": entry_price * 0.95, "max1440": entry_price * 1.10,
    }
    for col in HORIZON_COL.values():
        row[col] = None
    for horizon_min, price in exit_map.items():
        row[HORIZON_COL[horizon_min]] = price
    return row


def dump_rows(n, exit_price=103.0, start_ts=1000.0):
    return [
        make_row(start_ts + i, "dump", 100.0, {60: exit_price}, pct=-6.0)
        for i in range(n)
    ]


class TestSignAndTrades(unittest.TestCase):
    def test_sign(self):
        self.assertEqual(_sign("FOLLOW", "pump"), 1)
        self.assertEqual(_sign("FOLLOW", "dump"), -1)
        self.assertEqual(_sign("FADE", "pump"), -1)
        self.assertEqual(_sign("FADE", "dump"), 1)

    def test_pnl_math(self):
        rows = [make_row(1000.0, "dump", 100.0, {60: 103.0})]
        trades = _trades(rows, "FADE", "price", "p60")
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0]["gross"], 3.0)  # long after dump
        trades = _trades(rows, "FOLLOW", "price", "p60")
        self.assertAlmostEqual(trades[0]["gross"], -3.0)  # short after dump

    def test_null_prices_skipped(self):
        rows = [make_row(1000.0, "dump", 100.0, {})]
        self.assertEqual(_trades(rows, "FADE", "price", "p60"), [])
        rows = [make_row(1000.0, "dump", 100.0, {60: 103.0})]
        self.assertEqual(_trades(rows, "FADE", "pe30", "p60"), [])  # pe30 is NULL


class TestPassBar(unittest.TestCase):
    def test_fade_dump_passes(self):
        results = _analyze_rows(dump_rows(120))
        cell = results["dump"]["FADE"]["print"][60]["net025"]
        self.assertEqual(cell.n, 120)
        self.assertAlmostEqual(cell.mean, 2.75)
        self.assertEqual(cell.winrate, 100.0)
        self.assertEqual(cell.verdict, "PASS")

    def test_fade_pump_fails_when_pumps_continue(self):
        rows = [make_row(1000.0 + i, "pump", 100.0, {60: 103.0}, pct=6.0)
                for i in range(120)]
        results = _analyze_rows(rows)
        cell = results["pump"]["FADE"]["print"][60]["net025"]
        self.assertAlmostEqual(cell.mean, -3.25)
        self.assertEqual(cell.verdict, "NO")

    def test_insufficient_data(self):
        results = _analyze_rows(dump_rows(50))
        cell = results["dump"]["FADE"]["print"][60]["net025"]
        self.assertEqual(cell.n, 50)
        self.assertEqual(cell.verdict, "N/A")

    def test_winrate_below_55_fails(self):
        rows = []
        for i in range(54):
            rows.append(make_row(1000.0 + i, "dump", 100.0, {60: 110.0}))
        for i in range(46):
            rows.append(make_row(2000.0 + i, "dump", 100.0, {60: 99.0}))
        results = _analyze_rows(rows)
        cell = results["dump"]["FADE"]["print"][60]["net025"]
        self.assertGreater(cell.mean, 0.30)  # mean ok
        self.assertLess(cell.winrate, 55.0)  # but winrate fails
        self.assertEqual(cell.verdict, "NO")

    def test_mean_ex_top5(self):
        # one huge winner must not carry the verdict alone:
        # ex-top-5% mean <= 0 while everything else passes -> NO
        rows = [make_row(1000.0, "dump", 100.0, {60: 600.0})]  # +500% gross
        rows += [make_row(1001.0 + i, "dump", 100.0, {60: 100.05})  # net -0.20%
                 for i in range(39)]
        rows += [make_row(1040.0 + i, "dump", 100.0, {60: 100.35})  # net +0.10%
                 for i in range(60)]
        results = _analyze_rows(rows)
        cell = results["dump"]["FADE"]["print"][60]["net025"]
        self.assertGreater(cell.mean, 0.30)
        self.assertGreater(cell.median, 0)
        self.assertGreaterEqual(cell.winrate, 55.0)
        self.assertGreater(cell.mean_2nd_half, 0)
        self.assertLessEqual(cell.mean_ex_top5, 0)
        self.assertEqual(cell.verdict, "NO")

    def test_mfe_mae(self):
        results = _analyze_rows(dump_rows(120))
        cell = results["dump"]["FADE"]["print"][60]["net025"]
        # long: MFE from max60=104, MAE from min60=99
        self.assertAlmostEqual(cell.mfe, 4.0)
        self.assertAlmostEqual(cell.mae, -1.0)


class TestCuts(unittest.TestCase):
    def test_cuts_present(self):
        rows = dump_rows(10) + [
            make_row(2000.0 + i, "pump", 100.0, {60: 103.0}, origin="bingx",
                     window_s=300, pct=9.0, cohort="B")
            for i in range(10)
        ]
        results = _analyze_rows(rows)
        for key in ("all", "dump", "pump", "venue:mexc", "venue:bingx",
                    "window:60", "window:300", "pct:<8", "pct:8-12",
                    "cohort:A", "cohort:B", "cohort:A:dump", "cohort:B:pump"):
            self.assertIn(key, results, key)
        self.assertEqual(results["venue:bingx"]["FADE"]["print"][60]["gross"].n, 10)


class TestRunAnalysis(unittest.TestCase):
    def test_empty_db(self):
        import tempfile, os
        from radar.journal import Journal
        tmpdir = tempfile.mkdtemp()
        j = Journal(os.path.join(tmpdir, "j.sqlite3"))
        report = run_analysis(j.db_path)
        self.assertEqual(report.n_cex, 0)
        self.assertIn("PASS нет", report.telegram_summary())

    def test_verdict_lines(self):
        rows = dump_rows(120)
        import tempfile, os
        from radar.journal import Journal
        tmpdir = tempfile.mkdtemp()
        j = Journal(os.path.join(tmpdir, "j.sqlite3"))
        for r in rows:
            j._conn.execute(
                "INSERT INTO alerts (ts, asset, origin, direction, pct, window_s,"
                " price, cohort, p60, min60, max60) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (r["ts"], "BTC", "mexc", "dump", -6.0, 60, 100.0, "A",
                 103.0, 99.0, 104.0),
            )
        j._conn.commit()
        report = run_analysis(j.db_path)
        summary = report.telegram_summary()
        self.assertIn("PASS", summary)
        self.assertIn("FADE по дампам", summary)
        self.assertGreater(len(report.markdown), 1000)
        self.assertIn("Red-team", report.markdown)


if __name__ == "__main__":
    unittest.main()
