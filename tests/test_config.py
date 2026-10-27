"""Tests for radar/config.py — validation, parsing, secret safety."""
import os
import tempfile
import unittest
from unittest import mock

from radar.config import Config, ConfigError, load_config

FAKE_TOKEN = "123456789:" + "A" * 35  # matches the token shape check


def _write_env(tmpdir: str, body: str) -> str:
    path = os.path.join(tmpdir, ".env")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return tmpdir


def _clean_env():
    """Environment with none of our variables set."""
    return mock.patch.dict(os.environ, {}, clear=True)


class TestLoadConfig(unittest.TestCase):
    def test_missing_token_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir, _clean_env():
            with self.assertRaises(ConfigError) as ctx:
                load_config(tmpdir)
            self.assertIn("TELEGRAM_BOT_TOKEN", str(ctx.exception))

    def test_valid_file_config(self):
        with tempfile.TemporaryDirectory() as tmpdir, _clean_env():
            _write_env(
                tmpdir,
                f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\n"
                "TELEGRAM_CHAT_IDS=111, 222\n"
                "COHORT=B\n",
            )
            cfg = load_config(tmpdir)
            self.assertEqual(cfg.bot_token, FAKE_TOKEN)
            self.assertEqual(cfg.chat_ids, ("111", "222"))
            self.assertEqual(cfg.cohort, "B")

    def test_env_overrides_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_env(tmpdir, f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\nCOHORT=A\n")
            with mock.patch.dict(os.environ, {"COHORT": "C"}):
                cfg = load_config(tmpdir)
            self.assertEqual(cfg.cohort, "C")

    def test_chat_ids_parsing_ignores_empties(self):
        with tempfile.TemporaryDirectory() as tmpdir, _clean_env():
            _write_env(
                tmpdir,
                f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\n"
                "TELEGRAM_CHAT_IDS=111, 222 ,,  333 ,\n",
            )
            cfg = load_config(tmpdir)
            self.assertEqual(cfg.chat_ids, ("111", "222", "333"))

    def test_bad_token_shape_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir, _clean_env():
            _write_env(tmpdir, "TELEGRAM_BOT_TOKEN=not-a-token\n")
            with self.assertRaises(ConfigError):
                load_config(tmpdir)

    def test_bad_log_level_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir, _clean_env():
            _write_env(
                tmpdir,
                f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\nLOG_LEVEL=VERBOSE\n",
            )
            with self.assertRaises(ConfigError):
                load_config(tmpdir)

    def test_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir, _clean_env():
            _write_env(tmpdir, f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\n")
            cfg = load_config(tmpdir)
            self.assertEqual(cfg.cohort, "A")
            self.assertEqual(cfg.chat_ids, ())
            self.assertEqual(cfg.log_level, "INFO")

    def test_quoted_and_comment_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir, _clean_env():
            _write_env(
                tmpdir,
                "# comment line\n"
                f'TELEGRAM_BOT_TOKEN="{FAKE_TOKEN}"\n'
                "TELEGRAM_CHAT_IDS='111,222'\n",
            )
            cfg = load_config(tmpdir)
            self.assertEqual(cfg.bot_token, FAKE_TOKEN)
            self.assertEqual(cfg.chat_ids, ("111", "222"))

    def test_repr_never_leaks_token(self):
        cfg = Config(
            bot_token=FAKE_TOKEN,
            chat_ids=("111",),
            cohort="A",
            log_level="INFO",
            service_dir="/tmp",
        )
        self.assertNotIn(FAKE_TOKEN, repr(cfg))
        self.assertIn("[REDACTED]", repr(cfg))

    def test_safe_summary_never_leaks_token(self):
        cfg = Config(
            bot_token=FAKE_TOKEN,
            chat_ids=("111", "222"),
            cohort="A",
            log_level="INFO",
            service_dir="/tmp",
        )
        summary = " ".join(cfg.safe_summary().values())
        self.assertNotIn(FAKE_TOKEN, summary)
        self.assertIn("2 configured", summary)


if __name__ == "__main__":
    unittest.main()
