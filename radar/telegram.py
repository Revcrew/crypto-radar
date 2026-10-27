"""Telegram delivery to the dedicated crypto bot.

Hard rule (TZ 0): alerts go ONLY to the configured bot. On any failure the
service logs the error and sends nowhere else — no fallbacks, ever.
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger("radar.telegram")


class TelegramSender:
    def __init__(
        self,
        token: str,
        chat_ids: tuple[str, ...],
        timeout: int = 15,
        session: requests.Session | None = None,
    ):
        self._token = token
        self._chat_ids = chat_ids
        self._timeout = timeout
        self._session = session or requests.Session()
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"

    def send_alert(self, text: str, keyboard: list[list[dict]]) -> dict[str, bool]:
        """Send to every configured chat. Returns {chat_id: ok}."""
        results: dict[str, bool] = {}
        for chat_id in self._chat_ids:
            results[chat_id] = self._send_one(chat_id, text, keyboard)
        return results

    def _send_one(self, chat_id: str, text: str, keyboard: list[list[dict]]) -> bool:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        try:
            resp = self._session.post(self._url, json=payload, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # transport / HTTP / JSON errors
            log.error("telegram send failed (no fallback): %s", type(exc).__name__)
            return False
        if not data.get("ok"):
            log.error("telegram API error (no fallback): %s", data.get("description"))
            return False
        return True
