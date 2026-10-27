#!/usr/bin/env python3
"""Single-file entry point for hosts that need a main file (e.g. wispbyte).

Equivalent to `python -m radar`. The service is configured via environment
variables: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS, COHORT, ...
"""
from radar.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
