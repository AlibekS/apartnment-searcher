"""HTTP-клиент с анти-бан мерами: ротация User-Agent, случайные паузы, ретраи.

Если установлен curl_cffi — используем его (имитирует TLS-отпечаток Chrome,
что помогает против защиты от ботов). Иначе — обычный requests.
"""
from __future__ import annotations

import logging
import random
import time

log = logging.getLogger(__name__)

try:  # pragma: no cover - зависит от окружения
    from curl_cffi import requests as _cffi_requests
    HAVE_CFFI = True
except Exception:  # noqa: BLE001
    _cffi_requests = None
    HAVE_CFFI = False

import requests

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
]


class HttpClient:
    def __init__(self, delay: tuple[float, float] = (2, 5), retries: int = 3, timeout: int = 30):
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self._last = 0.0
        self.requests_made = 0
        if HAVE_CFFI:
            self.session = _cffi_requests.Session(impersonate="chrome")
        else:
            self.session = requests.Session()

    def _sleep(self) -> None:
        wait = random.uniform(*self.delay)
        elapsed = time.monotonic() - self._last
        if elapsed < wait:
            time.sleep(wait - elapsed)

    def get(self, url: str, referer: str | None = None) -> str | None:
        for attempt in range(1, self.retries + 1):
            self._sleep()
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,kk;q=0.8,en;q=0.6",
            }
            if referer:
                headers["Referer"] = referer
            try:
                resp = self.session.get(url, headers=headers, timeout=self.timeout)
                self._last = time.monotonic()
                self.requests_made += 1
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code == 404:
                    log.info("404 %s", url)
                    return None
                log.warning("HTTP %s на %s (попытка %d)", resp.status_code, url, attempt)
                if resp.status_code in (403, 429):
                    time.sleep(15 * attempt + random.uniform(0, 10))
            except Exception as exc:  # noqa: BLE001
                self._last = time.monotonic()
                log.warning("Ошибка запроса %s: %s (попытка %d)", url, exc, attempt)
                time.sleep(5 * attempt)
        return None
