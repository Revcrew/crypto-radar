"""Entry point: python -m radar [--check-config] [--once] [--dry-run]."""
from __future__ import annotations

import os
import sys

if __name__ == "__main__" and __package__ is None:
    # Executed as a plain script (e.g. `python radar/__main__.py`):
    # re-enter through the package so relative imports resolve.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from radar.__main__ import main as _package_main

    raise SystemExit(_package_main())

import argparse

from .config import ConfigError, load_config
from .engine import Engine
from .logging_setup import setup_logging
from .proxy import load_proxy_url


def cmd_check_config(service_dir: str | None = None) -> int:
    try:
        config = load_config(service_dir)
    except ConfigError as exc:
        print(f"config: ERROR: {exc}", file=sys.stderr)
        return 2
    print("config: OK")
    for key, val in config.safe_summary().items():
        print(f"  {key}: {val}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="radar", description="Crypto Crime Radar — price-spike detector"
    )
    parser.add_argument(
        "--check-config", action="store_true", help="validate configuration and exit"
    )
    parser.add_argument(
        "--service-dir", default=None, help="service directory containing .env"
    )
    parser.add_argument(
        "--once", action="store_true", help="run a single poll cycle and exit"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="format alerts but do not send them to Telegram",
    )
    args = parser.parse_args(argv)

    if args.check_config:
        return cmd_check_config(args.service_dir)

    try:
        config = load_config(args.service_dir)
    except ConfigError as exc:
        print(f"startup: ERROR: {exc}", file=sys.stderr)
        return 2

    secrets: tuple[str, ...] = config.secret_values()
    proxy_url = load_proxy_url(config.service_dir)
    if proxy_url:
        secrets = secrets + (proxy_url,)
    setup_logging(config.log_level, secrets)

    engine = Engine(config, dry_run=args.dry_run)
    engine.run(once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
