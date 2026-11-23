"""Парсер Krisha.kz.

Страница поиска: карточки `div.a-card[data-id]`. Заголовок карточки вида
«2-комнатная квартира · 54 м² · 3/5 этаж», в превью текста —
«жил. комплекс X, кирпичный дом, 2012 г.п., ...».
Страница объявления: блок `<script id="jsdata">window.data = {...}</script>`
(фото, координаты) + элементы `.offer__info-item[data-name=...]`.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import (BaseSource, clean_text, empty_record, extract_json_after,
                   find_video, to_float, to_int)

log = logging.getLogger(__name__)
BASE = "https://krisha.kz"

WALL_RE = re.compile(r"(кирпичн\w*|панельн\w*|монолитн\w*|каркасн\w*|блочн\w*|деревянн\w*|иное)\s+дом", re.I)
YEAR_RE = re.compile(r"(19\d{2}|20\d{2})\s*г\.\s*п\.?", re.I)
COMPLEX_RE = re.compile(r"жил\.\s*комплекс\s+([^,\n]+)", re.I)


def parse_title(title: str, rec: dict) -> None:
    if not title:
        return
    m = re.search(r"(\d+)\s*-\s*комн", title, re.I)
    if m:
        rec["rooms"] = int(m.group(1))
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*м²", title)
    if m:
        rec["area"] = float(m.group(1).replace(",", "."))
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*этаж", title)
    if m:
        rec["floor"], rec["floors_total"] = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"(\d+)\s*этаж", title)
        if m:
            rec["floor"] = int(m.group(1))


def parse_preview(text: str, rec: dict) -> None:
    if not text:
        return
    m = WALL_RE.search(text)
    if m and not rec.get("wall"):
        rec["wall"] = m.group(1).lower()
    m = YEAR_RE.search(text)
    if m and not rec.get("year"):
        rec["year"] = int(m.group(1))
    m = COMPLEX_RE.search(text)
    if m and not rec.get("complex"):
        rec["complex"] = m.group(1).strip()


def _upsize_photo(url: str) -> str:
    # превью вида ...-280x175.webp / ...-120x90.jpg → полноразмерное ...-full.jpg
    return re.sub(r"-\d{2,4}x\d{2,4}\.(webp|jpg|jpeg)$", r"-full.jpg", url)


class KrishaSource(BaseSource):
    name = "krisha"

    def fetch_list(self) -> list[dict]:
        records: dict[str, dict] = {}
        max_pages = int(self.settings.get("max_pages", 5))
        for base_url in self.search_urls():
            for page in range(1, max_pages + 1):
                sep = "&" if "?" in base_url else "?"
                url = base_url if page == 1 else f"{base_url}{sep}page={page}"
                html = self.http.get(url, referer=BASE + "/")
                if not html:
                    break
                items = self.parse_list_page(html)
                log.info("krisha: %s — %d объявлений", url, len(items))
                new = 0
                for rec in items:
                    if rec["source_id"] not in records:
                        new += 1
                    records[rec["source_id"]] = rec
                if not items or new == 0:
                    break
        return list(records.values())

    @staticmethod
    def parse_list_page(html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        out = []
        for card in soup.select("div.a-card[data-id]"):
            try:
                sid = card.get("data-id")
                rec = empty_record("krisha", sid)
                a = card.select_one("a.a-card__title") or card.select_one("a[href*='/a/show/']")
                rec["title"] = a.get_text(" ", strip=True) if a else None
                rec["url"] = urljoin(BASE, a["href"]) if a and a.get("href") else f"{BASE}/a/show/{sid}"
                parse_title(rec["title"] or "", rec)
                price = card.select_one(".a-card__price")
                rec["price"] = to_int(price.get_text()) if price else None
                sub = card.select_one(".a-card__subtitle")
                rec["address"] = sub.get_text(" ", strip=True) if sub else None
                prev = card.select_one(".a-card__text-preview")
                preview = prev.get_text(" ", strip=True) if prev else ""
                rec["description"] = preview
                parse_preview(preview, rec)
                stats = [s.get_text(" ", strip=True) for s in card.select(".a-card__stats-item")]
                if stats:
                    rec["city"] = stats[0]
                img = card.select_one("img[src]")
                if img:
                    src = img.get("src") or img.get("data-src")
                    if src and src.startswith("http"):
                        rec["photos"] = [_upsize_photo(src)]
                if card.select_one(".a-card__video, .a-card__label--video, [class*='video']"):
                    rec["video"] = rec["url"]
                rec["seller"] = "owner" if card.select_one(".label--owner, .owners__label") else None
                if rec["price"] and rec["source_id"]:
                    out.append(rec)
            except Exception as exc:  # noqa: BLE001
                log.warning("krisha: не удалось разобрать карточку: %s", exc)
        return out

    def needs_details(self, rec: dict) -> bool:
        return not rec.get("_details_loaded")

    def fetch_details(self, rec: dict) -> dict:
        html = self.http.get(rec["url"], referer=BASE + "/prodazha/kvartiry/")
        if not html:
            return rec
        self.parse_detail_page(html, rec)
        rec["_details_loaded"] = True
        return rec

    @staticmethod
    def parse_detail_page(html: str, rec: dict) -> dict:
        soup = BeautifulSoup(html, "lxml")
        data = extract_json_after(html, r"window\.data\s*=") or extract_json_after(html, r"var\s+data\s*=") or {}
        advert = data.get("advert") if isinstance(data, dict) else None
        if isinstance(advert, dict):
            photos = []
            for p in advert.get("photos") or []:
                src = p.get("src") if isinstance(p, dict) else p
                if src:
                    photos.append(src)
            if photos:
                rec["photos"] = photos
            mp = advert.get("map") or {}
            rec["lat"] = to_float(mp.get("lat")) or rec.get("lat")
            rec["lon"] = to_float(mp.get("lon")) or rec.get("lon")
            rec["price"] = to_int(advert.get("price")) or rec.get("price")
            rec["rooms"] = to_int(advert.get("rooms")) or rec.get("rooms")
            rec["area"] = to_float(advert.get("square")) or rec.get("area")
            if advert.get("addressTitle"):
                rec["address"] = advert["addressTitle"]
            if advert.get("title"):
                rec["title"] = advert["title"]
            if advert.get("hasVideo") or advert.get("videoUrl") or advert.get("video"):
                v = advert.get("videoUrl") or advert.get("video")
                rec["video"] = v if isinstance(v, str) and v.startswith("http") else rec["url"]

        if not rec.get("photos") or len(rec["photos"]) < 2:
            urls = [el.get("data-photo-url") for el in soup.select("[data-photo-url]") if el.get("data-photo-url")]
            if urls:
                rec["photos"] = list(dict.fromkeys(urls))

        # характеристики
        for item in soup.select(".offer__info-item[data-name], .offer__parameters dl, [data-name]"):
            name = item.get("data-name") or ""
            val_el = item.select_one(".offer__advert-short-info, dd")
            if not name:
                dt = item.select_one("dt[data-name]")
                name = dt.get("data-name") if dt else ""
            if not val_el or not name:
                continue
            val = val_el.get_text(" ", strip=True)
            if name == "flat.building" and val:
                m = re.match(r"([А-Яа-яё]+)", val)
                rec["wall"] = (m.group(1) if m else val).lower()
                yr = YEAR_RE.search(val) or re.search(r"(19\d{2}|20\d{2})", val)
                if yr and not rec.get("year"):
                    rec["year"] = int(yr.group(1))
            elif name == "house.year":
                rec["year"] = to_int(val) or rec.get("year")
            elif name == "flat.floor":
                m = re.search(r"(\d+)\s*из\s*(\d+)", val)
                if m:
                    rec["floor"], rec["floors_total"] = int(m.group(1)), int(m.group(2))
            elif name == "live.square":
                rec["area"] = to_float(val) or rec.get("area")
                m = re.search(r"кухни\s*[—-]?\s*(\d+(?:[.,]\d+)?)", val)
                if m:
                    rec["kitchen_area"] = float(m.group(1).replace(",", "."))
            elif name == "map.complex":
                rec["complex"] = val
            elif name == "map.city":
                rec["city"] = val
            elif name in ("flat.heating", "house.heating"):
                rec["heating"] = val

        desc_el = soup.select_one(".js-description, .offer__description .text, .a-text-white-spaces")
        if desc_el:
            rec["description"] = clean_text(desc_el.decode_contents())
        city_el = soup.select_one(".offer__location span, .offer__location")
        if city_el and not rec.get("address"):
            rec["address"] = city_el.get_text(" ", strip=True)
        if not rec.get("video"):
            rec["video"] = find_video(html)
        return rec
