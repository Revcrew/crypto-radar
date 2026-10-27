"""Tests for radar/checkpoint.py: 4h counter, one-off trigger."""
import os
import tempfile
import unittest
from unittest import mock

import radar.analysis
import radar.checkpoint
from radar.checkpoint import Checkpoint
from radar.state import StateStore


class FakeJournal:
    def __init__(self, n, db_path="/tmp/x.sqlite3"):
        self._n = n
        self.db_path = db_path

    def count(self, table):
        assert table == "alerts"
        return self._n


class FakeSender:
    def __init__(self):
        self.calls = []

    def send_alert(self, text, keyboard):
        self.calls.append((text, keyboard))
        return {"111": True}


class StubReport:
    def telegram_summary(self):
        return "SUMMARY"

    def write_markdown(self, path):
        with open(path, "w") as fh:
            fh.write("MD")


def make_checkpoint(n, tmpdir):
    state = StateStore(os.path.join(tmpdir, "state.json"))
    sender = FakeSender()
    journal = FakeJournal(n)
    cp = Checkpoint(journal, state, sender, tmpdir, dry_run=False)
    return cp, sender, state


class TestCheckpoint(unittest.TestCase):
    def test_waiting_below_target(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cp, sender, state = make_checkpoint(10, tmpdir)
            self.assertEqual(cp.check(now=1000.0), "waiting")
            self.assertEqual(sender.calls, [])
            self.assertFalse(state.get_section("checkpoint").get("triggered"))

    def test_trigger_once(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(radar.analysis, "run_analysis",
                                  return_value=StubReport()):
                cp, sender, state = make_checkpoint(500, tmpdir)
                self.assertEqual(cp.check(now=1000.0), "triggered")
                self.assertEqual(len(sender.calls), 1)
                text, keyboard = sender.calls[0]
                self.assertEqual(text, "SUMMARY")
                md_path = os.path.join(tmpdir, "ANALYSIS-500.md")
                self.assertTrue(os.path.exists(md_path))
                with open(md_path) as fh:
                    self.assertEqual(fh.read(), "MD")
                # second check: already, no resend
                self.assertEqual(cp.check(now=2000.0), "already")
                self.assertEqual(len(sender.calls), 1)

    def test_flag_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(radar.analysis, "run_analysis",
                                  return_value=StubReport()):
            cp, sender, state = make_checkpoint(500, tmpdir)
            cp.check(now=1000.0)
            # new objects, same state file
            cp2, sender2, _ = make_checkpoint(600, tmpdir)
            self.assertEqual(cp2.check(now=2000.0), "already")
            self.assertEqual(sender2.calls, [])


if __name__ == "__main__":
    unittest.main()
