"""Парсер OLX.kz.

OLX отдаёт состояние страницы в `window.__PRERENDERED_STATE__ = "<json-строка>"`.
В нём listing.listing.ads[] с полным описанием, параметрами, фото и координатами —
поэтому отдельные страницы объявлений обычно не нужны.
Запасной вариант — разбор HTML-карточек `[data-cy="l-card"]`.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseSource, clean_text, empty_record, find_video, to_float, to_int

log = logging.getLogger(__name__)
BASE = "https://www.olx.kz"


def _prerendered_state(html: str) -> dict | None:
    m = re.search(r"window\.__PRERENDERED_STATE__\s*=\s*(\"(?:\\.|[^\"\\])*\")", html)
    if m:
        try:
            return json.loads(json.loads(m.group(1)))
        except ValueError:
            pass
    m = re.search(r"window\.__PRERENDERED_STATE__\s*=\s*(\{)", html)
    if m:
        try:
            obj, _ = json.JSONDecoder().raw_decode(html[m.start(1):])
            return obj
        except ValueError:
            pass
    return None


def _photo(url: str) -> str:
    return url.replace("{width}", "1000").replace("{height}", "750")


PARAM_MAP = [
    ("rooms", ("количество комнат", "комнат", "number_of_rooms", "rooms")),
    ("area", ("общая площадь", "total_area", "площадь")),
    ("kitchen_area", ("площадь кухни", "kitchen")),
    ("floors_total", ("этажность", "total_floors", "floors_in_building", "floor_count")),
    ("floor", ("этаж", "floor")),
    ("year", ("год постройки", "year", "built")),
    ("wall", ("тип строения", "материал", "тип дома", "building_type", "wall")),
    ("heating", ("отоплен", "heating")),
    ("complex", ("жилой комплекс", "жк", "residential_complex", "complex")),
]


def _apply_params(params: list, rec: dict) -> None:
    for p in params or []:
        name = f"{p.get('name', '')} {p.get('key', '')}".lower()
        val = p.get("normalizedValue") or p.get("value")
        if isinstance(val, dict):
            val = val.get("label") or val.get("value") or val.get("key")
        if val in (None, ""):
            continue
        for field, keys in PARAM_MAP:
            if rec.get(field) is not None:
                continue
            if any(k in name for k in keys):
                # «этаж» не должен перекрыть «этажность»
                if field == "floor" and ("этажност" in name or "total" in name):
                    continue
                if field in ("rooms", "floor", "floors_total", "year"):
                    rec[field] = to_int(val)
                elif field in ("area", "kitchen_area"):
                    rec[field] = to_float(val)
                else:
                    rec[field] = str(val).strip()
                break


def ad_to_record(ad: dict) -> dict | None:
    sid = ad.get("id")
    if not sid:
        return None
    rec = empty_record("olx", sid)
    rec["title"] = ad.get("title")
    rec["url"] = ad.get("url") or f"{BASE}/d/obyavlenie/ID{sid}.html"
    price = ad.get("price") or {}
    reg = price.get("regularPrice") if isinstance(price, dict) else None
    if isinstance(reg, dict):
        rec["price"] = to_int(reg.get("value"))
    if not rec["price"] and isinstance(price, dict):
        rec["price"] = to_int(price.get("displayValue"))
    rec["description"] = clean_text(ad.get("description"))
    loc = ad.get("location") or {}
    city = loc.get("cityName")
    if not city and isinstance(loc.get("city"), dict):
        city = loc["city"].get("name")
    rec["city"] = city
    district = loc.get("districtName")
    if not district and isinstance(loc.get("district"), dict):
        district = loc["district"].get("name")
    rec["district"] = district
    region = loc.get("regionName")
    rec["address"] = ", ".join([x for x in (region, rec["city"], rec["district"]) if x])
    mp = ad.get("map") or {}
    rec["lat"], rec["lon"] = to_float(mp.get("lat")), to_float(mp.get("lon"))
    rec["photos"] = [_photo(p if isinstance(p, str) else p.get("link", "")) for p in ad.get("photos") or []]
    rec["photos"] = [p for p in rec["photos"] if p]
    rec["published_at"] = ad.get("createdTime") or ad.get("lastRefreshTime")
    rec["seller"] = "agency" if ad.get("isBusiness") else "owner"
    _apply_params(ad.get("params"), rec)
    rec["video"] = find_video(rec["description"])
    if rec["title"]:
        m = re.search(r"(\d+)\s*-?\s*комн", rec["title"], re.I)
        if m and not rec.get("rooms"):
            rec["rooms"] = int(m.group(1))
    return rec


class OlxSource(BaseSource):
    name = "olx"

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
                log.info("olx: %s — %d объявлений", url, len(items))
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
        out = []
        state = _prerendered_state(html)
        ads = []
        if isinstance(state, dict):
            ads = ((state.get("listing") or {}).get("listing") or {}).get("ads") or []
        for ad in ads:
            try:
                rec = ad_to_record(ad)
                if rec and rec["price"]:
                    out.append(rec)
            except Exception as exc:  # noqa: BLE001
                log.warning("olx: не удалось разобрать объявление: %s", exc)
        if out:
            return out
        # запасной вариант — HTML
        soup = BeautifulSoup(html, "lxml")
        for card in soup.select('[data-cy="l-card"]'):
            try:
                sid = card.get("id")
                a = card.select_one("a[href]")
                if not sid or not a:
                    continue
                rec = empty_record("olx", sid)
                rec["url"] = urljoin(BASE, a["href"])
                title = card.select_one("h4, h6, [data-cy='ad-card-title']")
                rec["title"] = title.get_text(" ", strip=True) if title else None
                price = card.select_one('[data-testid="ad-price"]')
                rec["price"] = to_int(price.get_text()) if price else None
                loc = card.select_one('[data-testid="location-date"]')
                if loc:
                    rec["address"] = loc.get_text(" ", strip=True).split(" - ")[0]
                    rec["city"] = rec["address"].split(",")[0].strip()
                img = card.select_one("img[src]")
                if img and img.get("src", "").startswith("http"):
                    rec["photos"] = [img["src"]]
                m = re.search(r"(\d+(?:[.,]\d+)?)\s*м²", card.get_text(" "))
                if m:
                    rec["area"] = float(m.group(1).replace(",", "."))
                if rec["title"]:
                    m = re.search(r"(\d+)\s*-?\s*комн", rec["title"], re.I)
                    if m:
                        rec["rooms"] = int(m.group(1))
                if rec["price"]:
                    rec["_needs_details"] = True
                    out.append(rec)
            except Exception as exc:  # noqa: BLE001
                log.warning("olx: не удалось разобрать карточку: %s", exc)
        return out

    def needs_details(self, rec: dict) -> bool:
        return bool(rec.get("_needs_details")) and not rec.get("_details_loaded")

    def fetch_details(self, rec: dict) -> dict:
        html = self.http.get(rec["url"], referer=BASE + "/")
        if not html:
            return rec
        state = _prerendered_state(html) or {}
        ad = ((state.get("ad") or {}).get("ad")) if isinstance(state, dict) else None
        if isinstance(ad, dict):
            full = ad_to_record(ad)
            if full:
                for k, v in full.items():
                    if v not in (None, "", []):
                        rec[k] = v
        rec["_details_loaded"] = True
        return rec
