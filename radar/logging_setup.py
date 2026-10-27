"""Logging with automatic secret redaction.

Every secret known to the config is replaced with [REDACTED] in log output,
so a token can never leak through an f-string someone forgot to sanitize.
"""
from __future__ import annotations

import logging

REDACTED = "[REDACTED]"


def sanitize(text: str, secrets: tuple[str, ...]) -> str:
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, REDACTED)
    return text


class SecretFilter(logging.Filter):
    """Replaces known secret values in the rendered log message."""

    def __init__(self, secrets: tuple[str, ...]):
        super().__init__()
        self._secrets = tuple(s for s in secrets if s)

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        clean = sanitize(rendered, self._secrets)
        if clean != rendered:
            record.msg = clean
            record.args = ()
        return True


def setup_logging(
    level: str = "INFO",
    secrets: tuple[str, ...] = (),
    name: str = "radar",
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
    secret_filter = SecretFilter(secrets)
    for handler in logger.handlers:
        handler.addFilter(secret_filter)
    return logger
