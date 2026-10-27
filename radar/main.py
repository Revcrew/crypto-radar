#!/usr/bin/env python3
"""Entry point for hosts that run radar/main.py directly as a script.

Adds the container root to sys.path so the `radar` package resolves,
then delegates to the canonical entry point (same as `python -m radar`).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from radar.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
