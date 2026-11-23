"""Демо-данные для проверки сайта до первого запуска парсера.
Проходят через тот же пайплайн, что и настоящие: чёрный список, ЖКХ-фильтр, склейку дублей,
историю цен. Будут автоматически заменены реальными данными при первом успешном парсинге."""
from __future__ import annotations

import random
from datetime import date, timedelta

from .sources.base import empty_record

ASTANA = [
    ("Есильский р-н", ["Хайвилл", "Highvill", "Нурсая", "Сезам", "Северное сияние"], ["Кабанбай батыра", "Туран", "Сыганак", "Достык"]),
    ("Алматы р-н", ["Жагалау", "Шугыла", "Ак Шагала"], ["Шарль де Голль", "Б. Момышулы", "Тлендиева"]),
    ("Сарыарка р-н", ["Жеруйык", "Сарыарка"], ["Сарыарка", "Иманова", "Жансугурова"]),
    ("Байконур р-н", ["Самал", "Дипломат"], ["Сейфуллина", "Ауэзова", "Абая"]),
    ("Нура р-н", ["Asyl Park", "Grand Turan"], ["Ұлы Дала", "Е-10", "Мангилик Ел"]),
]
SUBURBS = [("Косшы", "Акмолинская обл., Целиноградский р-н, Косшы"),
           ("Ильинка", "Акмолинская обл., Целиноградский р-н, с. Ильинка (Уркер)"),
           ("Жибек Жолы", "Акмолинская обл., Аршалынский р-н, с. Жибек Жолы"),
           ("Коянды", "Акмолинская обл., Целиноградский р-н, с. Коянды"),
           ("Пригородный", "Акмолинская обл., Целиноградский р-н, с. Пригородный")]
WALLS = ["кирпичный", "панельный", "монолитный", "кирпичный", "монолитный"]
GOOD = [
    "Продаётся уютная квартира в хорошем состоянии. Центральное отопление, центральная вода и канализация. "
    "Кирпичный дом, тёплая, светлая. Рядом школа, детский сад, остановка. Не в залоге, без обременений. Собственник.",
    "Квартира с качественным ремонтом, остаётся кухонный гарнитур и встроенная техника. Центральное отопление. "
    "Двор закрытый, видеонаблюдение. Подходит под ипотеку Отбасы. Торг уместен.",
    "Срочно! Чистая квартира, пластиковые окна, ламинат. Автономное отопление, газовый котел. "
    "Документы готовы, один собственник. Возможен обмен.",
]
BAD = [
    "Дом в дачном массиве, вода привозная, септик. Отопление на твердом топливе, зимой тепло. Документы в порядке.",
    "Квартира в новом доме, сдача в 2027 году, договор долевого участия. Черновая отделка.",
    "Хорошая квартира, не в залоге, но есть нюансы с документами. Печное отопление в подсобке.",
]


def _photos(seed: str, n: int) -> list[str]:
    return [f"https://picsum.photos/seed/{seed}-{i}/960/640" for i in range(n)]


def make_demo() -> tuple[dict, dict]:
    rnd = random.Random(2026)
    today = date.today()
    raw: dict[str, dict] = {}
    history: dict[str, list] = {}

    def add(source: str, sid: int, **kw) -> dict:
        rec = empty_record(source, str(sid))
        rec.update(kw)
        rec["url"] = ("https://krisha.kz/prodazha/kvartiry/astana/" if source == "krisha"
                      else "https://www.olx.kz/nedvizhimost/prodazha-kvartiry/astana/")
        rec["_demo"] = True
        days_ago = rnd.randint(0, 20)
        rec["first_seen"] = (today - timedelta(days=days_ago)).isoformat()
        rec["last_seen"] = today.isoformat()
        raw[f"{source}:{sid}"] = rec
        return rec

    sid = 900000
    for i in range(30):
        sid += 1
        district, complexes, streets = rnd.choice(ASTANA)
        rooms = rnd.choices([1, 2, 3, 4], weights=[3, 6, 3, 1])[0]
        area = round(rnd.uniform(28, 38) + (rooms - 1) * rnd.uniform(14, 20), 1)
        floor = rnd.choices([1, 2, 3, 4, 5, 7, 9], weights=[2, 4, 4, 2, 2, 1, 1])[0]
        floors_total = max(floor, rnd.choice([5, 5, 9, 12, 16]))
        sqm = rnd.randint(330, 520) * 1000
        price = round(area * sqm / 100000) * 100000
        year = rnd.choice([1978, 1995, 2004, 2008, 2012, 2016, 2019, 2021, 2023])
        cx = rnd.choice(complexes)
        street = f"{rnd.choice(streets)} {rnd.randint(1, 80)}"
        src = rnd.choice(["krisha", "krisha", "olx"])
        wall = rnd.choice(WALLS)
        add(src, sid, title=f"{rooms}-комнатная квартира · {area} м² · {floor}/{floors_total} этаж",
            price=price, rooms=rooms, area=area, floor=floor, floors_total=floors_total, year=year,
            wall=wall, city="Астана", complex=f"ЖК {cx}", address=f"{district}, {street}",
            description=rnd.choice(GOOD), photos=_photos(f"a{sid}", rnd.randint(3, 7)),
            video="https://www.youtube.com/watch?v=dQw4w9WgXcQ" if i % 7 == 0 else None,
            seller=rnd.choice(["owner", "agency"]))
        # снижение цены у части объявлений
        if i % 5 == 0:
            old = price + rnd.choice([800000, 1500000, 2500000])
            history[f"{src}:{sid}"] = [
                {"date": (today - timedelta(days=14)).isoformat(), "price": old + 500000},
                {"date": (today - timedelta(days=6)).isoformat(), "price": old},
            ]
        # дубль той же квартиры на другой площадке
        if i % 6 == 1:
            sid += 1
            other = "olx" if src == "krisha" else "krisha"
            add(other, sid, title=f"Продам {rooms}-комн. квартиру, {cx}",
                price=price + rnd.choice([-300000, 200000, 500000]), rooms=rooms,
                area=round(area + rnd.uniform(-1.2, 1.2), 1), floor=floor, floors_total=floors_total,
                city="Астана", complex=f"ЖК {cx}", address=f"{district}",
                description="Продаю квартиру. Звоните, отвечу на все вопросы. " + rnd.choice(GOOD),
                photos=_photos(f"b{sid}", 2), seller="agency")

    budget_cheap = [(2, 52.0, 2, 17500000), (2, 48.5, 3, 16900000), (2, 56.0, 1, 18800000),
                    (1, 38.0, 2, 12500000), (3, 74.0, 3, 23500000), (2, 60.2, 2, 19900000)]
    for j, (name, addr) in enumerate(SUBURBS):
        for k in range(3):
            sid += 1
            rooms, area, floor, price = budget_cheap[(j + k) % len(budget_cheap)]
            bad = (j == 1 and k == 2) or (j == 3 and k == 1)
            desc = BAD[0] if (j == 1 and k == 2) else (BAD[1] if (j == 3 and k == 1) else rnd.choice(GOOD))
            add(rnd.choice(["krisha", "olx"]), sid,
                title=f"{rooms}-комнатная квартира · {area} м² · {floor}/5 этаж",
                price=price - k * 400000, rooms=rooms, area=area, floor=floor, floors_total=5,
                year=rnd.choice([2010, 2015, 2018, 2020, 2022]) if not bad else 2027 if j == 3 else 2008,
                wall=rnd.choice(WALLS), city=name, address=f"{addr}, ул. {rnd.choice(['Абая', 'Достык', 'Байтерек', 'Жастар'])} {rnd.randint(1, 40)}",
                description=desc, photos=_photos(f"s{sid}", rnd.randint(2, 5)), seller="owner")
            if k == 0:
                history[f"{list(raw)[-1]}"] = [{"date": (today - timedelta(days=9)).isoformat(), "price": price + 3200000}]

    # эти объявления должны быть отброшены чёрным списком / белым списком географии
    sid += 1
    add("krisha", sid, title="2-комнатная квартира · 55 м² · 2/5 этаж", price=11000000, rooms=2, area=55, floor=2,
        floors_total=5, city="Талапкер", address="Акмолинская обл., с. Талапкер", description=GOOD[0], photos=[])
    sid += 1
    add("olx", sid, title="2-комн. квартира", price=9000000, rooms=2, area=50, floor=1, floors_total=2,
        city="Кызылсуат", address="Акмолинская обл., Кызылсуат", description=GOOD[0], photos=[])
    sid += 1
    add("krisha", sid, title="2-комнатная квартира · 50 м² · 2/5 этаж", price=9000000, rooms=2, area=50, floor=2,
        floors_total=5, city="Кокшетау", address="Кокшетау", description=GOOD[0], photos=[])
    return raw, history
