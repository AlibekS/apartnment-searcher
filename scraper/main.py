"""Точка входа парсера.

    python -m scraper.main            # собрать объявления со всех площадок и обновить data/
    python -m scraper.main --offline  # только пересобрать data/listings.json из кэша (без интернета)
    python -m scraper.main --demo     # сгенерировать демо-данные для проверки сайта
    python -m scraper.main --sources krisha --verbose
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from .http_client import HAVE_CFFI, HttpClient
from .pipeline import Rules, build_listings, classify_location, now_iso
from .sources import SOURCES

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"
log = logging.getLogger("scraper")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path: Path, obj, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":") if compact else None,
                      indent=None if compact else 1)
    path.write_text(text + "\n", encoding="utf-8")


def load_rules() -> Rules:
    return Rules(
        load_json(CONFIG / "locations.json", {}),
        load_json(CONFIG / "blacklist.json", {}),
        load_json(CONFIG / "stopwords.json", {}),
        load_json(CONFIG / "default_filters.json", {}),
    )


def merge_record(old: dict | None, new: dict, today: str) -> dict:
    if not old:
        new["first_seen"] = today
        new["last_seen"] = today
        return new
    merged = dict(old)
    for k, v in new.items():
        if v not in (None, "", []):
            # короткое превью со страницы поиска не должно затирать полное описание
            if k == "description" and old.get("_details_loaded") and len(str(v)) < len(old.get("description") or ""):
                continue
            if k == "photos" and len(v) < len(old.get("photos") or []):
                continue
            merged[k] = v
    merged["last_seen"] = today
    return merged


def scrape(source_names: list[str], raw: dict, settings: dict, rules: Rules, status: dict) -> None:
    today = date.today().isoformat()
    http = HttpClient(delay=tuple(settings.get("delay_seconds", [2, 5])))
    log.info("HTTP-клиент: %s", "curl_cffi (имитация Chrome)" if HAVE_CFFI else "requests")
    detail_budget = int(settings.get("max_detail_requests_per_run", 100))
    max_price = int(settings.get("max_price", 30000000))
    drop_after = int(settings.get("drop_after_days", 4))

    for name in source_names:
        cls = SOURCES.get(name)
        src_settings = settings.get(name, {})
        if not cls or not src_settings.get("enabled", True):
            continue
        src = cls(http, src_settings, settings)
        st = {"ok": False, "found": 0, "kept": 0, "details": 0, "error": None, "at": now_iso()}
        try:
            items = src.fetch_list()
            st["found"] = len(items)
            kept = []
            for rec in items:
                if rec.get("price") and rec["price"] > max_price:
                    continue
                region, _, _ = classify_location(rec, rules)
                if not region:
                    continue  # чужой город / чёрный список — даже не храним
                key = f"{name}:{rec['source_id']}"
                raw[key] = merge_record(raw.get(key), rec, today)
                kept.append(key)
            st["kept"] = len(kept)
            # подробности — сначала для новых объявлений
            todo = [k for k in kept if src.needs_details(raw[k])]
            todo.sort(key=lambda k: raw[k].get("first_seen") or "", reverse=True)
            # «Предохранитель»: если сайт подряд отказывает в страницах объявлений (403 и т.п.),
            # прекращаем догрузку в этом запуске — данных со страниц поиска достаточно для сайта.
            fails_in_row, attempts = 0, 0
            deadline = time.monotonic() + float(settings.get("max_detail_minutes", 15)) * 60
            for key in todo[:max(detail_budget, 0)]:
                if fails_in_row >= 3:
                    st["details_blocked"] = True
                    log.warning("%s: страницы объявлений недоступны (%d отказа подряд) — пропускаем догрузку подробностей",
                                name, fails_in_row)
                    break
                if time.monotonic() > deadline:
                    log.info("%s: лимит времени на подробности исчерпан", name)
                    break
                attempts += 1
                try:
                    raw[key] = src.fetch_details(raw[key])
                    st["details"] += 1
                    fails_in_row = 0
                except Exception as exc:  # noqa: BLE001
                    fails_in_row += 1
                    log.warning("%s: детали %s не загружены: %s", name, key, exc)
            detail_budget -= attempts
            st["ok"] = len(items) > 0
            if not items:
                st["error"] = "0 объявлений — возможно, сайт поменял вёрстку или заблокировал запросы"
        except Exception as exc:  # noqa: BLE001
            log.exception("%s: ошибка", name)
            st["error"] = str(exc)[:300]
        status[name] = st
        log.info("%s: %s", name, st)

        # устаревшие объявления удаляем только если площадка отработала успешно
        if st["ok"]:
            limit = (date.today() - timedelta(days=drop_after)).isoformat()
            for key in [k for k, v in raw.items() if v.get("source") == name and (v.get("last_seen") or "") < limit]:
                del raw[key]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Сбор объявлений о квартирах (Астана и пригород)")
    ap.add_argument("--offline", action="store_true", help="не ходить в интернет, пересобрать из кэша")
    ap.add_argument("--demo", action="store_true", help="сгенерировать демо-данные")
    ap.add_argument("--sources", default=",".join(SOURCES), help="через запятую: krisha,olx")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    rules = load_rules()
    settings = load_json(CONFIG / "sources.json", {})
    prev_meta = load_json(DATA / "meta.json", {})

    if args.demo:
        from .demo import make_demo
        raw, history = make_demo()
        status = {"demo": {"ok": True, "found": len(raw), "kept": len(raw), "error": None, "at": now_iso()}}
        demo = True
    else:
        raw = load_json(DATA / "raw_listings.json", {})
        history = load_json(DATA / "price_history.json", {})
        # если раньше были демо-данные — начинаем с чистого листа
        if prev_meta.get("demo") and not args.offline:
            raw, history = {}, {}
        status = dict(prev_meta.get("sources") or {})
        status.pop("demo", None)
        demo = False
        if not args.offline:
            scrape([s.strip() for s in args.sources.split(",") if s.strip()], raw, settings, rules, status)

    if not demo and not args.offline and not any(v.get("ok") for v in status.values()) and prev_meta and not prev_meta.get("demo"):
        log.error("Ни одна площадка не ответила — оставляем прежние данные без изменений.")
        prev_meta["last_attempt_at"] = now_iso()
        prev_meta["sources"] = status
        save_json(DATA / "meta.json", prev_meta)
        return 0

    listings, stats = build_listings(raw, history, rules)
    # история цен только для живых объявлений
    history = {k: v for k, v in history.items() if k in raw}

    updated = now_iso()
    meta = {
        "updated_at": updated,
        "last_attempt_at": updated,
        "demo": demo,
        "count": len(listings),
        "stats": stats,
        "sources": status,
    }
    if not demo:
        save_json(DATA / "raw_listings.json", raw, compact=True)
    save_json(DATA / "price_history.json", history, compact=True)
    save_json(DATA / "listings.json", {"updated_at": updated, "demo": demo, "listings": listings}, compact=True)
    save_json(DATA / "meta.json", meta)
    log.info("Готово: %d карточек (%s)", len(listings), stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
