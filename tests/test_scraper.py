"""Запуск: python -m unittest discover -s tests"""
import json
import unittest

from scraper.main import load_rules
from scraper.pipeline import build_listings, classify_location, find_terms
from scraper.sources.base import empty_record
from scraper.sources.krisha import KrishaSource
from scraper.sources.olx import OlxSource

RULES = load_rules()

KRISHA_LIST = """
<div class="a-card a-storage-live" data-id="700111">
  <div class="a-card__image"><picture><img src="https://alakt-photos-kr.kcdn.kz/webp/aa/bb/1-280x175.webp"></picture></div>
  <a class="a-card__title" href="/a/show/700111">2-комнатная квартира · 54.5 м² · 3/9 этаж</a>
  <div class="a-card__price">18 900 000 〒</div>
  <div class="a-card__subtitle">Есильский р-н, Кабанбай батыра 40</div>
  <div class="a-card__text-preview">жил. комплекс Хайвилл, монолитный дом, 2015 г.п., состояние: хорошее</div>
  <div class="a-card__stats"><div class="a-card__stats-item">Астана</div><div class="a-card__stats-item">вчера</div></div>
</div>
"""

KRISHA_DETAIL = """<html><script id="jsdata">window.data = {"advert":{"id":700111,"price":18500000,"rooms":2,
"square":54.5,"addressTitle":"Есильский р-н, Кабанбай батыра 40","photos":[{"src":"https://x/1-full.jpg"},{"src":"https://x/2-full.jpg"}],
"map":{"lat":51.12,"lon":71.43}}};</script>
<div class="offer__info-item" data-name="flat.building"><div class="offer__info-title">Тип дома</div><div class="offer__advert-short-info">монолитный, 2015 г.п.</div></div>
<div class="js-description">Центральное отопление.<br>Рядом школа. Видео: https://youtu.be/abcdefghijk</div></html>"""

OLX_AD = {
    "id": 555, "title": "2-комнатная квартира, 52 м², 2/5 этаж", "url": "https://www.olx.kz/d/obyavlenie/x-ID555.html",
    "description": "Продам. Септик и вода привозная.", "price": {"regularPrice": {"value": 15000000}},
    "location": {"cityName": "Косшы", "regionName": "Акмолинская область"},
    "photos": ["https://ireland.apollo.olxcdn.com/v1/files/abc/image;s={width}x{height}"],
    "params": [
        {"key": "number_of_rooms", "name": "Количество комнат", "value": "2", "normalizedValue": "2"},
        {"key": "total_area", "name": "Общая площадь", "value": "52 м²", "normalizedValue": "52"},
        {"key": "floor", "name": "Этаж", "value": "2", "normalizedValue": "2"},
        {"key": "total_floors", "name": "Этажность дома", "value": "5", "normalizedValue": "5"},
        {"key": "year", "name": "Год постройки", "value": "2018", "normalizedValue": "2018"},
    ],
}


class ParserTests(unittest.TestCase):
    def test_krisha_list_and_detail(self):
        recs = KrishaSource.parse_list_page(KRISHA_LIST)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual((r["rooms"], r["area"], r["floor"], r["floors_total"]), (2, 54.5, 3, 9))
        self.assertEqual((r["price"], r["year"], r["wall"], r["complex"], r["city"]),
                         (18900000, 2015, "монолитный", "Хайвилл", "Астана"))
        KrishaSource.parse_detail_page(KRISHA_DETAIL, r)
        self.assertEqual(r["price"], 18500000)
        self.assertEqual(len(r["photos"]), 2)
        self.assertEqual((r["lat"], r["lon"]), (51.12, 71.43))
        self.assertIn("Центральное отопление", r["description"])
        self.assertTrue(r["video"].startswith("https://youtu.be/"))

    def test_olx_prerendered(self):
        state = {"listing": {"listing": {"ads": [OLX_AD]}}}
        html = "<script>window.__PRERENDERED_STATE__= " + json.dumps(json.dumps(state)) + ";</script>"
        recs = OlxSource.parse_list_page(html)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual((r["rooms"], r["area"], r["floor"], r["floors_total"], r["year"]), (2, 52.0, 2, 5, 2018))
        self.assertEqual(r["city"], "Косшы")
        self.assertIn("1000x750", r["photos"][0])


class PipelineTests(unittest.TestCase):
    def rec(self, source, sid, **kw):
        r = empty_record(source, sid)
        r.update(first_seen="2026-11-01", last_seen="2026-11-20", url="https://example.com")
        r.update(kw)
        return r

    def test_geo(self):
        self.assertEqual(classify_location({"city": "Астана", "address": "ул. Косшыгулулы 5"}, RULES)[0], "astana")
        self.assertEqual(classify_location({"city": "Косшы"}, RULES)[:2], ("suburb", "Косшы"))
        self.assertIsNone(classify_location({"city": "Талапкер"}, RULES)[0])
        self.assertIsNone(classify_location({"city": "Кокшетау"}, RULES)[0])
        self.assertEqual(classify_location({"address": "Акмолинская обл., с. Уркер"}, RULES)[0], "suburb")
        self.assertIsNone(classify_location({"city": "Ильинка", "address": "Костанайская область, Ильинка"}, RULES)[0])

    def test_stopwords_boundaries(self):
        self.assertEqual(find_terms("Сдача дома в 2025, передача ключей", RULES.utility), [])
        self.assertIn("дача", find_terms("Продаётся дача", RULES.utility))

    def test_dedup_and_drop(self):
        raw = {
            "krisha:1": self.rec("krisha", 1, price=19000000, rooms=2, area=54.0, floor=3, city="Астана",
                                 complex="Хайвилл", address="Есильский р-н, Туран 1", description="Центральное отопление"),
            "olx:9": self.rec("olx", 9, price=18500000, rooms=2, area=55.2, floor=3, city="Астана",
                              complex="ЖК Хайвилл", description="септик"),
            "krisha:2": self.rec("krisha", 2, price=17000000, rooms=2, area=54.0, floor=4, city="Астана",
                                 complex="Хайвилл", address="Есильский р-н"),
        }
        history = {"krisha:2": [{"date": "2026-11-10", "price": 21000000}]}
        cards, stats = build_listings(raw, history, RULES, today="2026-11-20")
        self.assertEqual(len(cards), 2)
        merged = next(c for c in cards if len(c["sources"]) == 2)
        self.assertEqual(merged["price"], 18500000)
        self.assertTrue(merged["has_utility_warning"])
        dropped = next(c for c in cards if c["price_drop"])
        self.assertEqual((dropped["price_drop"]["old"], dropped["price_drop"]["new"]), (21000000, 17000000))
        self.assertTrue(dropped["price_drop"]["entered_budget"])


if __name__ == "__main__":
    unittest.main()
