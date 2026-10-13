"""Alerts: always logged; also sent to Telegram when configured. Never raises."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("desk")


class Notifier:
    def __init__(self, http: httpx.Client | None = None, token: str | None = None, chat_id: str | None = None):
        self._http = http
        self._token = token
        self._chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self._http and self._token and self._chat_id)

    def send(self, text: str) -> None:
        log.warning("AVISO: %s", text)
        if not self.enabled:
            return
        try:
            resp = self._http.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={"chat_id": self._chat_id, "text": text[:4000]},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            # Only the exception type: the message would contain the URL with the bot token.
            log.error("falha ao enviar aviso pelo Telegram (%s)", type(exc).__name__)
