"""Configuration loading and validation.

Secrets (bot token, proxy credentials) live ONLY in files with mode 600
(.env, proxy.env) inside the service directory on the VDS. They are never
printed, logged, committed to the repo or included in reports — only facts
like "set (46 chars)" may appear.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

REQUIRED_VARS = ("TELEGRAM_BOT_TOKEN",)

_KNOWN_VARS = REQUIRED_VARS + (
    "TELEGRAM_CHAT_IDS",
    "COHORT",
    "LOG_LEVEL",
)

_DEFAULTS = {
    "TELEGRAM_CHAT_IDS": "",
    "COHORT": "A",
    "LOG_LEVEL": "INFO",
}

# Telegram bot tokens look like "<digits>:<35-char secret>".
_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


def _parse_dotenv(path: str) -> dict[str, str]:
    """Minimal .env parser (stdlib only): KEY=VALUE, # comments, quotes."""
    values: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            if not key:
                raise ConfigError(f"{path}:{lineno}: empty variable name")
            values[key] = val
    return values


@dataclass(frozen=True)
class Config:
    bot_token: str
    chat_ids: tuple[str, ...]
    cohort: str
    log_level: str
    service_dir: str

    def secret_values(self) -> tuple[str, ...]:
        """All values that must never appear in logs/reports."""
        return (self.bot_token,)

    def safe_summary(self) -> dict[str, str]:
        """Non-secret facts about the configuration (safe to log)."""
        return {
            "cohort": self.cohort,
            "chat_ids": f"{len(self.chat_ids)} configured",
            "bot_token": f"set ({len(self.bot_token)} chars)",
            "log_level": self.log_level,
            "service_dir": self.service_dir,
        }

    def __repr__(self) -> str:  # never leak the token
        return (
            "Config(bot_token='[REDACTED]', "
            f"chat_ids={self.chat_ids!r}, cohort={self.cohort!r}, "
            f"log_level={self.log_level!r}, service_dir={self.service_dir!r})"
        )


def _default_service_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config(service_dir: str | None = None) -> Config:
    """Load config: defaults <- .env file <- real environment (wins)."""
    service_dir = (
        service_dir
        or os.environ.get("RADAR_SERVICE_DIR")
        or _default_service_dir()
    )
    env_path = os.path.join(service_dir, ".env")
    file_values = _parse_dotenv(env_path) if os.path.exists(env_path) else {}

    def get(key: str) -> str:
        if key in os.environ:
            return os.environ[key]
        if key in file_values:
            return file_values[key]
        return _DEFAULTS.get(key, "")

    missing = [k for k in REQUIRED_VARS if not get(k)]
    if missing:
        raise ConfigError(
            "missing required configuration: " + ", ".join(missing)
            + f" (set them in {env_path} with mode 600)"
        )

    token = get("TELEGRAM_BOT_TOKEN")
    if not _TOKEN_RE.match(token):
        raise ConfigError(
            "TELEGRAM_BOT_TOKEN does not look like a Telegram bot token"
        )

    chat_ids = tuple(
        part for part in (p.strip() for p in get("TELEGRAM_CHAT_IDS").split(","))
        if part
    )
    level = get("LOG_LEVEL").upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ConfigError(
            f"LOG_LEVEL must be DEBUG/INFO/WARNING/ERROR/CRITICAL, got {level!r}"
        )
    cohort = get("COHORT").strip() or "A"

    return Config(
        bot_token=token,
        chat_ids=chat_ids,
        cohort=cohort,
        log_level=level,
        service_dir=service_dir,
    )
