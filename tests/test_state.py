"""Tests for radar/state.py — persistence roundtrip and safety."""
import json
import os
import tempfile
import unittest

from radar.state import StateStore


class TestStateStore(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "state", "state.json")
            s = StateStore(path)
            dex = s.get_section("dex")
            dex["seeded"] = True
            dex["cooldowns"] = {"solana:abc": 123.0}
            s.save()

            s2 = StateStore(path)
            self.assertTrue(s2.get_section("dex")["seeded"])
            self.assertEqual(s2.get_section("dex")["cooldowns"], {"solana:abc": 123.0})
            self.assertEqual(s2.get_section("news"), {})

    def test_corrupt_file_starts_fresh(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "state.json")
            with open(path, "w") as fh:
                fh.write("{not json")
            s = StateStore(path)
            self.assertEqual(s.get_section("dex"), {})

    def test_missing_file_starts_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = StateStore(os.path.join(tmpdir, "nope", "state.json"))
            self.assertEqual(s.get_section("news"), {})

    def test_save_writes_valid_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "state.json")
            s = StateStore(path)
            s.get_section("news")["seen"] = ["a", "b"]
            s.save()
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["news"]["seen"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
