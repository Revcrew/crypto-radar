"""Persistent service state (JSON, atomic writes).

Holds DEX cooldowns/seed flags and news dedup hashes across restarts.
Lives in <service_dir>/state/ (gitignored).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile

log = logging.getLogger("radar.state")


class StateStore:
    def __init__(self, path: str):
        self._path = path
        self._data: dict = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding="utf-8") as fh:
                    self._data = json.load(fh)
            except (OSError, ValueError) as exc:
                log.warning("state file unreadable, starting fresh: %s", exc)
                self._data = {}
        if not isinstance(self._data, dict):
            self._data = {}

    def get_section(self, name: str) -> dict:
        """Return the mutable dict for a section (creates it if missing)."""
        self._ensure_loaded()
        section = self._data.get(name)
        if not isinstance(section, dict):
            section = {}
            self._data[name] = section
        return section

    def save(self) -> None:
        """Atomically write state to disk."""
        self._ensure_loaded()
        directory = os.path.dirname(self._path)
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=1)
            os.replace(tmp_path, self._path)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
