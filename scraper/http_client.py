"""HTTP-клиент с анти-бан мерами: «маскировка» под браузер, случайные паузы, ретраи.

Если установлен curl_cffi — используем его (имитирует TLS-отпечаток настоящего браузера,
что помогает против защиты от ботов). Иначе — обычный requests.

Стратегии маскировки перебираются ПО КАЖДОМУ САЙТУ отдельно: если сайт ответил 403,
клиент переключается на следующую стратегию (свежая сессия, другой «браузер») и пробует
ещё раз. Удачная стратегия запоминается до конца запуска.
"""
from __future__ import annotations

import logging
import random
import time
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

try:  # pragma: no cover - зависит от окружения
    from curl_cffi import requests as _cffi_requests
    HAVE_CFFI = True
except Exception:  # noqa: BLE001
    _cffi_requests = None
    HAVE_CFFI = False

import requests

CHROME_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"

# (имя, профиль curl_cffi, свой User-Agent или None = UA профиля)
STRATEGIES = [
    ("chrome+ua", "chrome", "chrome_ua"),       # так страницы поиска Krisha открывались в первом запуске
    ("chrome", "chrome", None),
    ("chrome131", "chrome131", None),
    ("edge", "edge", None),
    ("safari", "safari", None),
    ("chrome124+ua", "chrome124", "chrome_ua"),
]


class HttpClient:
    def __init__(self, delay: tuple[float, float] = (2, 5), retries: int = 3, timeout: int = 30):
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self._last = 0.0
        self.requests_made = 0
        self.last_status: int | None = None
        self._host_strategy: dict[str, int] = {}
        self._sessions: dict[tuple[str, int], object] = {}
        self._ua = random.choice(CHROME_UAS)

    @property
    def mode(self) -> str:
        return "curl_cffi (имитация браузера)" if HAVE_CFFI else "requests"

    # ---------------------------------------------------------------- сессии
    def _session(self, host: str, idx: int):
        key = (host, idx)
        if key not in self._sessions:
            if HAVE_CFFI:
                try:
                    self._sessions[key] = _cffi_requests.Session(impersonate=STRATEGIES[idx][1])
                except Exception as exc:  # noqa: BLE001 — профиль не поддерживается этой версией curl_cffi
                    log.debug("профиль %s недоступен: %s", STRATEGIES[idx][1], exc)
                    self._sessions[key] = None
            else:
                self._sessions[key] = requests.Session()
        return self._sessions[key]

    def _headers(self, idx: int, referer: str | None) -> dict:
        h = {"Accept-Language": "ru-RU,ru;q=0.9,kk;q=0.8,en;q=0.6"}
        if not HAVE_CFFI or STRATEGIES[idx][2]:
            h["User-Agent"] = self._ua
            h["Accept"] = BROWSER_ACCEPT
        if referer:
            h["Referer"] = referer
        return h

    def _rotate(self, host: str) -> bool:
        """Переключиться на следующую стратегию для сайта. False — стратегии кончились."""
        if not HAVE_CFFI:
            return False
        idx = self._host_strategy.get(host, 0)
        while idx + 1 < len(STRATEGIES):
            idx += 1
            if self._session(host, idx) is not None:
                self._host_strategy[host] = idx
                log.info("%s: переключаюсь на маскировку «%s»", host, STRATEGIES[idx][0])
                return True
        return False

    def _sleep(self) -> None:
        wait = random.uniform(*self.delay)
        elapsed = time.monotonic() - self._last
        if elapsed < wait:
            time.sleep(wait - elapsed)

    # ---------------------------------------------------------------- запросы
    def get(self, url: str, referer: str | None = None, retries: int | None = None,
            rotate_on_403: bool = True) -> str | None:
        """Возвращает HTML или None. Код последнего ответа — в self.last_status.

        rotate_on_403=False — для второстепенных страниц (объявления): не тратим на них стратегии,
        чтобы не «сжечь» рабочую маскировку для страниц поиска.
        """
        host = urlsplit(url).netloc
        retries = self.retries if retries is None else retries
        self.last_status = None
        attempt = 0
        rotations = 0
        while attempt < retries:
            attempt += 1
            idx = self._host_strategy.get(host, 0)
            session = self._session(host, idx)
            if session is None and not self._rotate(host):
                return None
            idx = self._host_strategy.get(host, 0)
            session = self._session(host, idx)
            self._sleep()
            try:
                resp = session.get(url, headers=self._headers(idx, referer), timeout=self.timeout)
                self._last = time.monotonic()
                self.requests_made += 1
                self.last_status = resp.status_code
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code == 403:
                    log.warning("HTTP 403 на %s (маскировка «%s»)", url, STRATEGIES[idx][0] if HAVE_CFFI else "requests")
                    if rotate_on_403 and rotations < 3 and self._rotate(host):
                        rotations += 1
                        attempt -= 1  # смена маскировки не считается попыткой
                        time.sleep(random.uniform(4, 9))
                        continue
                    return None
                if resp.status_code in (404, 410):
                    log.warning("HTTP %s на %s", resp.status_code, url)
                    return None
                log.warning("HTTP %s на %s (попытка %d)", resp.status_code, url, attempt)
                if resp.status_code == 429:
                    time.sleep(20 * attempt + random.uniform(0, 10))
            except Exception as exc:  # noqa: BLE001
                self._last = time.monotonic()
                log.warning("Ошибка запроса %s: %s (попытка %d)", url, exc, attempt)
                time.sleep(5 * attempt)
        return None

    def get_json(self, url: str, referer: str | None = None):
        import json
        text = self.get(url, referer=referer, retries=2)
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None
