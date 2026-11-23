from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from ..http_client import HttpClient

# Единый формат «сырого» объявления, который возвращает каждый модуль площадки.
RAW_FIELDS = (
    "source", "source_id", "url", "title", "price", "rooms", "area", "kitchen_area",
    "floor", "floors_total", "year", "wall", "heating", "city", "district", "complex",
    "address", "lat", "lon", "description", "photos", "video", "published_at", "seller",
)


def empty_record(source: str, source_id: str) -> dict[str, Any]:
    rec = {k: None for k in RAW_FIELDS}
    rec.update(source=source, source_id=str(source_id), photos=[])
    return rec


def to_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"\d+(?:[.,]\d+)?", str(value).replace(" ", " "))
    return float(m.group(0).replace(",", ".")) if m else None


def clean_text(value: Any) -> str:
    if not value:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", str(value), flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def extract_json_after(text: str, marker_regex: str) -> Any | None:
    """Находит JSON-объект, идущий сразу после маркера (например `window.data =`)."""
    m = re.search(marker_regex, text)
    if not m:
        return None
    start = text.find("{", m.end())
    if start == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
        return obj
    except ValueError:
        return None


YOUTUBE_RE = re.compile(r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)[\w\-]{6,}[^\s\"'<>]*")


def find_video(text: str) -> str | None:
    if not text:
        return None
    m = YOUTUBE_RE.search(text)
    return m.group(0) if m else None


class BaseSource(ABC):
    name = "base"

    def __init__(self, http: HttpClient, settings: dict, global_settings: dict):
        self.http = http
        self.settings = settings
        self.global_settings = global_settings

    def search_urls(self) -> list[tuple[str, int]]:
        """Ссылки поиска с лимитом страниц. Элемент конфига — строка или {"url": ..., "max_pages": N}."""
        max_price = self.global_settings.get("max_price", 30000000)
        default_pages = int(self.settings.get("max_pages", 5))
        out = []
        for item in self.settings.get("search_urls", []):
            url, pages = (item.get("url", ""), int(item.get("max_pages", default_pages))) if isinstance(item, dict) \
                else (item, default_pages)
            if url:
                out.append((url.replace("{max_price}", str(max_price)), pages))
        return out

    def paginate(self, base_url: str, max_pages: int, referer: str, parse) -> dict[str, dict]:
        """Листает страницы поиска. Останавливается, если страница пустая, без новых объявлений
        или заметно короче первой (значит, это последняя страница — дальше сайты часто подмешивают
        «похожие» объявления из других разделов)."""
        records: dict[str, dict] = {}
        first = 0
        for page in range(1, max_pages + 1):
            sep = "&" if "?" in base_url else "?"
            url = base_url if page == 1 else f"{base_url}{sep}page={page}"
            html = self.http.get(url, referer=referer)
            if not html:
                break
            items = parse(html)
            logging.getLogger(__name__).info("%s: %s — %d объявлений", self.name, url, len(items))
            new = 0
            for rec in items:
                if rec["source_id"] not in records:
                    new += 1
                records[rec["source_id"]] = rec
            first = first or len(items)
            if not items or new == 0 or len(items) < first * 0.3:
                break
        return records

    @abstractmethod
    def fetch_list(self) -> list[dict]:
        """Собрать объявления со страниц поиска."""

    def needs_details(self, rec: dict) -> bool:
        return False

    def fetch_details(self, rec: dict) -> dict:
        """Догрузить подробности (описание, фото, характеристики). По умолчанию — ничего."""
        return rec
