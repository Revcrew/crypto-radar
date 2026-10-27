"""Tests for radar/logging_setup.py — secrets must never reach log output."""
import io
import logging
import unittest

from radar.logging_setup import REDACTED, SecretFilter, sanitize, setup_logging

SECRET = "tok-secret-value-0123456789"


class TestSanitize(unittest.TestCase):
    def test_replaces_secret(self):
        self.assertEqual(
            sanitize(f"token={SECRET} ok", (SECRET,)), f"token={REDACTED} ok"
        )

    def test_multiple_occurrences(self):
        self.assertEqual(sanitize(f"{SECRET}/{SECRET}", (SECRET,)), f"{REDACTED}/{REDACTED}")

    def test_no_secret_no_change(self):
        self.assertEqual(sanitize("plain message", (SECRET,)), "plain message")

    def test_empty_secrets_ignored(self):
        self.assertEqual(sanitize(f"a{SECRET}b", ("", SECRET)), f"a{REDACTED}b")


class TestSecretFilter(unittest.TestCase):
    def _record(self, msg, *args):
        return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)

    def test_filter_redacts_rendered_message(self):
        f = SecretFilter((SECRET,))
        rec = self._record("sending with %s", SECRET)
        self.assertTrue(f.filter(rec))
        self.assertNotIn(SECRET, rec.getMessage())
        self.assertIn(REDACTED, rec.getMessage())

    def test_filter_redacts_fstring_style(self):
        f = SecretFilter((SECRET,))
        rec = self._record(f"url uses {SECRET} here")
        f.filter(rec)
        self.assertNotIn(SECRET, rec.getMessage())


class TestSetupLogging(unittest.TestCase):
    def test_handler_output_redacts_secret(self):
        stream = io.StringIO()
        logger = logging.getLogger("radar.test.redact")
        logger.handlers.clear()
        logger.propagate = False
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.addFilter(SecretFilter((SECRET,)))

        logger.warning("alert via bot token=%s", SECRET)
        out = stream.getvalue()
        self.assertNotIn(SECRET, out)
        self.assertIn(REDACTED, out)

    def test_setup_logging_installs_filter(self):
        logger = setup_logging("INFO", (SECRET,), name="radar.test.setup")
        try:
            filters = [
                f for h in logger.handlers for f in h.filters
                if isinstance(f, SecretFilter)
            ]
            self.assertTrue(filters, "SecretFilter not installed on handlers")
        finally:
            logger.handlers.clear()


if __name__ == "__main__":
    unittest.main()
