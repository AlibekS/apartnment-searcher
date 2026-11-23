"""ETL-пайплайн: география + чёрный список, ЖКХ-фильтр, вторичка/стройка,
склейка дублей, аналитика цены (цена за м², отклонение от медианы), история цен."""
from __future__ import annotations

import re
import statistics
from datetime import date, datetime, timezone
from typing import Any


# ---------------------------------------------------------------- текст

def norm(text: Any) -> str:
    if not text:
        return ""
    t = str(text).lower().replace("ё", "е").replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


def compile_terms(terms: list[str], whole_word: bool = False) -> list[tuple[str, re.Pattern]]:
    """Стоп-слова ищем с границей слова слева (чтобы «дача» не ловила «сдача»),
    а справа — по префиксу («дача» ловит «дачный»). Для топонимов — целое слово."""
    out = []
    for term in terms:
        t = norm(term)
        if not t:
            continue
        right = r"(?!\w)" if whole_word else ""
        out.append((term, re.compile(r"(?<!\w)" + re.escape(t) + right)))
    return out


def find_terms(text: str, compiled: list[tuple[str, re.Pattern]]) -> list[str]:
    t = norm(text)
    found = []
    for term, rx in compiled:
        if rx.search(t) and term not in found:
            found.append(term)
    return found


# ---------------------------------------------------------------- конфиг

class Rules:
    def __init__(self, locations: dict, blacklist: dict, stopwords: dict, default_filters: dict):
        city = locations.get("city", {})
        self.city_name = city.get("name", "Астана")
        self.city_aliases = compile_terms(city.get("aliases", [self.city_name]), whole_word=True)
        self.suburb_region = compile_terms(locations.get("suburb_region", {}).get("aliases", []))
        self.suburbs = []
        for s in locations.get("suburbs", []):
            self.suburbs.append({
                "name": s["name"],
                "aliases": compile_terms(s.get("aliases", [s["name"]]), whole_word=True),
                "require_any": compile_terms(s.get("require_any", [])),
            })
        self.bl_localities = compile_terms(blacklist.get("localities", []), whole_word=True)
        self.bl_complexes = compile_terms(blacklist.get("complexes", []))
        self.bl_streets = compile_terms(blacklist.get("streets", []))
        self.utility = compile_terms(stopwords.get("utility", []))
        self.new_building = compile_terms(stopwords.get("new_building", []))
        self.budget = default_filters.get("budget") or default_filters.get("price_max") or 20000000


# ---------------------------------------------------------------- география

DISTRICT_RE = re.compile(r"([А-ЯЁA-Z][\wё\-]+)\s+(?:р-н|район)", re.I)


def location_text(rec: dict) -> str:
    return " | ".join(str(rec.get(k) or "") for k in ("city", "district", "address", "complex", "title"))


def classify_location(rec: dict, rules: Rules) -> tuple[str | None, str | None, str]:
    """Возвращает (region, locality, reason). region: 'astana' | 'suburb' | None (исключить)."""
    loc = location_text(rec)
    hit = find_terms(loc, rules.bl_localities)
    if hit:
        return None, None, f"blacklist:locality:{hit[0]}"
    hit = find_terms(" ".join(str(rec.get(k) or "") for k in ("complex", "title", "address")), rules.bl_complexes)
    if hit:
        return None, None, f"blacklist:complex:{hit[0]}"
    hit = find_terms(rec.get("address") or "", rules.bl_streets)
    if hit:
        return None, None, f"blacklist:street:{hit[0]}"

    city = rec.get("city") or ""
    if find_terms(city, rules.city_aliases):
        return "astana", rules.city_name, "city"
    foreign_region = bool(re.search(r"обл\.|област", norm(loc))) and rules.suburb_region \
        and not find_terms(loc, rules.suburb_region)
    for s in rules.suburbs:
        if find_terms(loc, s["aliases"]):
            if foreign_region:
                return None, None, f"other_region:{s['name']}"
            if s["require_any"]:
                text = loc + " " + (rec.get("description") or "")
                if not find_terms(text, s["require_any"]):
                    return None, None, f"suburb_not_verified:{s['name']}"
            return "suburb", s["name"], "suburb"
    if find_terms(loc, rules.city_aliases):
        return "astana", rules.city_name, "address"
    return None, None, "not_in_whitelist"


DISTRICT_CANON = [
    ("алмат", "Алматы р-н"), ("есил", "Есиль р-н"), ("сарыар", "Сарыарка р-н"),
    ("байкон", "Байконур р-н"), ("нура", "Нура р-н"), ("нұра", "Нура р-н"), ("сарайш", "Сарайшык р-н"),
]


def canon_district(name: str | None) -> str | None:
    """Krisha пишет «Алматы р-н», OLX — «Алматинский район»: приводим к одному виду."""
    if not name:
        return None
    n = norm(name)
    for prefix, canon in DISTRICT_CANON:
        if n.startswith(prefix):
            return canon
    return str(name).strip()


def extract_district(rec: dict) -> str | None:
    if rec.get("district"):
        return canon_district(rec["district"])
    m = DISTRICT_RE.search(rec.get("address") or "")
    return canon_district(f"{m.group(1)} р-н") if m else None


def house_address(rec: dict) -> str:
    """Улица + номер дома (если есть цифры), без района — для отсева «разных квартир в одном ЖК»."""
    addr = norm(rec.get("address"))
    addr = re.sub(r"^[^,]*(?:р-н|район)\s*,?\s*", "", addr)
    return addr if re.search(r"\d", addr) else ""


# ---------------------------------------------------------------- характеристики

HEATING_RULES = [
    ("центральное", r"центральн\w* отоплен|отоплен\w* центральн|тэц"),
    ("автономное (газ)", r"газов\w* (?:котел|отоплен)|отоплен\w* газ|автономн\w* газ"),
    ("автономное (электро)", r"электрокотел|электро\w* отоплен|отоплен\w* электр|теплый пол"),
    ("автономное", r"автономн\w* отоплен|индивидуальн\w* отоплен|свой котел|собственн\w* котел"),
    ("твёрдое топливо", r"тверд\w* топлив|твердотоплив|печн\w* отоплен|на угле|угольн\w* котел"),
]

WALL_NORMAL = {
    "кирпич": "кирпич", "панел": "панель", "монолит": "монолит", "каркас": "каркас",
    "блоч": "блок", "газоблок": "блок", "дерев": "дерево",
}


def infer_heating(rec: dict) -> str | None:
    if rec.get("heating"):
        return str(rec["heating"])
    text = norm(rec.get("description"))
    for label, rx in HEATING_RULES:
        if re.search(rx, text):
            return label
    return None


def normalize_wall(wall: Any) -> str | None:
    w = norm(wall)
    if not w:
        return None
    for k, v in WALL_NORMAL.items():
        if k in w:
            return v
    return w


def market_type(rec: dict, rules: Rules) -> tuple[str, list[str]]:
    text = f"{rec.get('title') or ''} {rec.get('description') or ''}"
    hits = find_terms(text, rules.new_building)
    year = rec.get("year")
    if year and year > date.today().year:
        hits.append(f"год постройки {year}")
    return ("new" if hits else "secondary"), hits


# ---------------------------------------------------------------- дубли

COMPLEX_IN_TEXT = re.compile(r"(?<!\w)(?:жк|жилой комплекс|жил\. комплекс)\s*[«\"“']?\s*([\w\-]+(?:\s[\w\-]+)?)", re.I)


def guess_complex(rec: dict) -> str:
    """ЖК из поля, а если его нет (OLX) — из заголовка/описания («ЖК Onyx …»)."""
    if rec.get("complex"):
        return norm(re.sub(r"^(?:жк|жилой комплекс)\s+", "", norm(rec["complex"])))
    m = COMPLEX_IN_TEXT.search(f"{rec.get('title') or ''} . {rec.get('description') or ''}")
    if not m:
        return ""
    words = norm(m.group(1)).split()
    # второе слово берём, только если это не служебное слово
    if len(words) > 1 and words[1] in {"на", "по", "в", "от", "с", "рядом", "возле", "район", "р-н"}:
        words = words[:1]
    return " ".join(words)


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"\w{3,}", norm(text))}


def text_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def _loc_key(rec: dict) -> tuple[str, str, str]:
    return rec.get("complex_key") or "", norm(rec.get("district_norm")), norm(rec.get("locality"))


def is_duplicate(a: dict, b: dict) -> bool:
    if a.get("rooms") != b.get("rooms") or a.get("floor") != b.get("floor"):
        return False
    if not a.get("area") or not b.get("area") or abs(a["area"] - b["area"]) > 1.5:
        return False
    ca, da, la = _loc_key(a)
    cb, db, lb = _loc_key(b)
    if la != lb:
        return False
    if da and db and da != db:
        return False
    # оба адреса с номером дома и они разные — это разные квартиры (например, разные блоки ЖК)
    ha, hb = house_address(a), house_address(b)
    if ha and hb and ha != hb:
        return False
    for f in ("floors_total", "year"):
        if a.get(f) and b.get(f) and a[f] != b[f]:
            return False
    pa, pb = a.get("price") or 0, b.get("price") or 0
    price_diff = abs(pa - pb) / max(pa, pb) if pa and pb else 1
    if ca and cb:
        if not (ca == cb or ca in cb or cb in ca):
            return False
        # в одном ЖК бывает много одинаковых планировок — цены должны быть близки
        return price_diff <= 0.12
    # ЖК неизвестен хотя бы у одного — склеиваем только почти точные совпадения
    if abs(a["area"] - b["area"]) > 0.5 or price_diff > 0.07:
        return False
    if a.get("source") == b.get("source"):
        # повтор на той же площадке (другой риелтор/перевыкладка) — тексты обычно почти одинаковые
        return text_similarity(f"{a.get('title')} {a.get('description')}", f"{b.get('title')} {b.get('description')}") >= 0.5
    # разные площадки: этажность дома должна быть известна у обоих
    return bool(a.get("floors_total") and b.get("floors_total"))


def cluster(records: list[dict]) -> list[list[dict]]:
    """Жадная кластеризация: объявление попадает в группу, только если оно — дубль КАЖДОГО её члена
    (без цепочек A≈B≈C, которые склеивали разные квартиры)."""
    buckets: dict[tuple, list[dict]] = {}
    groups: list[list[dict]] = []
    for r in records:
        if r.get("rooms") is None or r.get("floor") is None or not r.get("area"):
            groups.append([r])
            continue
        buckets.setdefault((r["rooms"], r["floor"], r.get("locality")), []).append(r)
    for items in buckets.values():
        local: list[list[dict]] = []
        for r in sorted(items, key=lambda x: x["price"]):
            for g in local:
                if all(is_duplicate(r, m) for m in g):
                    g.append(r)
                    break
            else:
                local.append([r])
        groups.extend(local)
    return groups


# ---------------------------------------------------------------- история цен

def update_history(history: dict, key: str, price: int, today: str) -> None:
    h = history.setdefault(key, [])
    if not h or h[-1]["price"] != price:
        h.append({"date": today, "price": price})


def price_drop_info(hist: list[dict], budget: int) -> dict | None:
    if len(hist) < 2:
        return None
    cur = hist[-1]["price"]
    prev = hist[-2]["price"]
    if cur >= prev:
        return None
    max_before = max(h["price"] for h in hist[:-1])
    return {
        "old": prev,
        "new": cur,
        "max_old": max_before,
        "date": hist[-1]["date"],
        "pct": round((cur - prev) / prev * 100, 1),
        "entered_budget": prev > budget >= cur,
    }


# ---------------------------------------------------------------- сборка

def _richness(r: dict) -> int:
    return len(r.get("photos") or []) * 3 + len(r.get("description") or "") // 50 + sum(
        1 for k in ("year", "wall", "complex", "lat", "floors_total") if r.get(k))


def build_listings(raw: dict[str, dict], history: dict, rules: Rules, today: str | None = None) -> tuple[list[dict], dict]:
    today = today or date.today().isoformat()
    stats = {"raw": len(raw), "excluded": {}, "utility_warnings": 0}
    prepared = []
    for key, rec in raw.items():
        if not rec.get("price"):
            continue
        region, locality, reason = classify_location(rec, rules)
        if not region:
            bucket = reason.split(":")[0]
            stats["excluded"][bucket] = stats["excluded"].get(bucket, 0) + 1
            continue
        r = dict(rec)
        r["key"] = key
        r["region"], r["locality"] = region, locality
        r["district_norm"] = extract_district(rec) if region == "astana" else locality
        r["complex_key"] = guess_complex(rec)
        r["wall"] = normalize_wall(rec.get("wall"))
        r["heating"] = infer_heating(rec)
        r["market"], r["market_triggers"] = market_type(rec, rules)
        r["utility_triggers"] = find_terms(f"{rec.get('title') or ''} {rec.get('description') or ''}", rules.utility)
        update_history(history, key, rec["price"], today)
        prepared.append(r)

    cards = []
    for group in cluster(prepared):
        group.sort(key=lambda r: (r["price"], r.get("first_seen") or ""))
        best = max(group, key=_richness)
        cheapest = group[0]
        photos = list(best.get("photos") or [])
        for r in group:
            for p in r.get("photos") or []:
                if p not in photos and len(photos) < 30:
                    photos.append(p)
        primary = min(group, key=lambda r: (r.get("first_seen") or "9999", r["key"]))
        drops = [(price_drop_info(history.get(r["key"], []), rules.budget), r) for r in group]
        drops = [(d, r) for d, r in drops if d]
        drop, drop_src = (max(drops, key=lambda x: x[0]["old"] - x[0]["new"]) if drops else (None, None))
        hist_src = drop_src or cheapest
        triggers = sorted({t for r in group for t in r["utility_triggers"]})
        market_triggers = sorted({t for r in group for t in r["market_triggers"]})
        card = {
            "id": primary["key"].replace(":", "-"),
            "keys": [r["key"] for r in group],
            "title": best.get("title"),
            "price": cheapest["price"],
            "rooms": best.get("rooms"),
            "area": best.get("area"),
            "kitchen_area": next((r["kitchen_area"] for r in group if r.get("kitchen_area")), None),
            "floor": best.get("floor"),
            "floors_total": next((r["floors_total"] for r in group if r.get("floors_total")), None),
            "year": next((r["year"] for r in group if r.get("year")), None),
            "wall": next((r["wall"] for r in group if r.get("wall")), None),
            "heating": next((r["heating"] for r in group if r.get("heating")), None),
            "region": best["region"],
            "locality": best["locality"],
            "district": best.get("district_norm") if best["region"] == "astana" else None,
            "complex": next((r["complex"] for r in group if r.get("complex")), None),
            "address": best.get("address"),
            "lat": next((r["lat"] for r in group if r.get("lat")), None),
            "lon": next((r["lon"] for r in group if r.get("lon")), None),
            "description": best.get("description") or "",
            "photos": photos,
            # запасные адреса фото: если «полноразмерное» не откроется, сайт покажет превью
            "photo_fallback": {p: t for r in group for p, t in zip(r.get("photos") or [], r.get("photo_thumbs") or [])
                               if p != t and p in photos},
            "video": next((r["video"] for r in group if r.get("video")), None),
            "market": "new" if any(r["market"] == "new" for r in group) else "secondary",
            "market_triggers": market_triggers,
            "has_utility_warning": bool(triggers),
            "utility_triggers": triggers,
            "sources": [{
                "site": r["source"], "id": r["source_id"], "url": r.get("url"), "price": r["price"],
                "seller": r.get("seller"), "first_seen": r.get("first_seen"), "last_seen": r.get("last_seen"),
            } for r in group],
            "first_seen": min((r.get("first_seen") or today) for r in group),
            "last_seen": max((r.get("last_seen") or today) for r in group),
            "price_drop": drop,
            "price_history": history.get(hist_src["key"], []),
        }
        card["price_per_sqm"] = round(card["price"] / card["area"]) if card.get("area") else None
        if card["has_utility_warning"]:
            stats["utility_warnings"] += 1
        cards.append(card)

    add_market_analytics(cards)
    cards.sort(key=lambda c: (c["region"] != "astana", c["first_seen"]), reverse=False)
    stats["cards"] = len(cards)
    stats["duplicates_merged"] = len(prepared) - len(cards)
    return cards, stats


def add_market_analytics(cards: list[dict]) -> None:
    """Отклонение цены за м² от медианы аналогичных квартир (комнатность + локация)."""
    def rooms_b(c):
        return min(c.get("rooms") or 0, 4)

    levels = [
        lambda c: (rooms_b(c), c["region"], c.get("district") or c["locality"]),
        lambda c: (rooms_b(c), c["region"], c["locality"]),
        lambda c: (rooms_b(c), c["region"]),
    ]
    groups: list[dict] = []
    for fn in levels:
        g: dict = {}
        for c in cards:
            if c.get("price_per_sqm"):
                g.setdefault(fn(c), []).append(c["price_per_sqm"])
        groups.append(g)
    for c in cards:
        c["market_median_sqm"] = None
        c["market_deviation_pct"] = None
        c["market_sample"] = 0
        if not c.get("price_per_sqm"):
            continue
        for fn, g in zip(levels, groups):
            vals = g.get(fn(c), [])
            if len(vals) >= 3:
                med = statistics.median(vals)
                c["market_median_sqm"] = round(med)
                c["market_deviation_pct"] = round((c["price_per_sqm"] / med - 1) * 100, 1)
                c["market_sample"] = len(vals)
                break


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
