import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException

from app.models import Place, Point
from app.itinerary_doc import build_itinerary
from app.opening_hours import opening_window, preferred_time, closed_for_date
from app.requirements_extract import extract_required_places, _decode_names, _fallback_names


class OpeningHoursTests(unittest.TestCase):
    def test_single_window_and_ambiguous_seasons(self):
        self.assertEqual(opening_window("每日 08:30—17:30").start, 510)
        self.assertEqual(opening_window("每日 08:30—17:30").end, 1050)
        self.assertIsNone(opening_window("夏季 08:30-17:30；冬季 09:00-17:00"))
        self.assertIsNone(opening_window("全天开放"))

    def test_night_preference_and_weekly_closure(self):
        self.assertEqual(preferred_time("一定要在晚上去一次黄鹤楼", "黄鹤楼"), "night")
        self.assertTrue(closed_for_date("周一闭馆，09:00-17:00", date(2026, 11, 23)))
        self.assertFalse(closed_for_date("周一闭馆，09:00-17:00", date(2026, 11, 24)))


class RequirementTests(unittest.IsolatedAsyncioTestCase):
    def test_model_extraction_parse(self):
        self.assertEqual(_decode_names('{"mustVisit":["黄鹤楼"]}'), ["黄鹤楼"])
        self.assertIn("黄鹤楼", _fallback_names("一定要在晚上去一次黄鹤楼"))
        self.assertEqual(_fallback_names("不想去黄鹤楼"), [])

    async def test_must_visit_requires_grounded_place(self):
        with patch("app.requirements_extract._model", new=AsyncMock(return_value='{"mustVisit":["黄鹤楼","不存在的云中景区"]}')), patch("app.requirements_extract.search_places", new=AsyncMock(return_value=([], "高德"))):
            result = await extract_required_places("一定要在晚上去一次黄鹤楼")
        self.assertIn("黄鹤楼", [p["name"] for p in result["places"]])
        self.assertEqual(result["unresolved"], ["不存在的云中景区"])


class ItineraryOpeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_conflict_with_night_request_is_explicit(self):
        place = Place(placeId="yellow", name="黄鹤楼", category="风景名胜", address="武汉", location=Point(lng=114.30, lat=30.54), source="高德地图")
        request = SimpleNamespace(mustVisitPlaces=[place], days=1, people=1, requirements="一定要在晚上去一次黄鹤楼", startDate="2026-11-22", transport="步行", lodging="", lodgingLocation=None)
        with (
            patch("app.itinerary_doc.settings.amap_web_service_key", "test"),
            patch("app.itinerary_doc.verify_required_place", new=AsyncMock(side_effect=lambda p: p)),
            patch("app.itinerary_doc.search_places", new=AsyncMock(return_value=([], "高德"))),
            patch("app.itinerary_doc._model", new=AsyncMock(return_value='{"days":[[{"placeId":"yellow","experience":"参观黄鹤楼","timeWindow":"evening"}]]}')),
            patch("app.itinerary_doc.collect_opening_hours", new=AsyncMock(return_value={"yellow": "08:30-17:30"})),
        ):
            with self.assertRaises(HTTPException) as failure:
                await build_itinerary(request)
        self.assertIn("开放", failure.exception.detail)

    async def test_verified_hours_shift_evening_visit_and_keep_it_last(self):
        evening = Place(placeId="e", name="夜间博物馆", category="风景名胜", address="武汉", location=Point(lng=114.30, lat=30.54), source="高德地图")
        daytime = Place(placeId="d", name="日间公园", category="风景名胜", address="武汉", location=Point(lng=114.31, lat=30.54), source="高德地图")
        request = SimpleNamespace(
            mustVisitPlaces=[evening, daytime], days=1, people=1,
            requirements="一定要晚上去夜间博物馆", startDate="2026-11-22",
            transport="步行", lodging="", lodgingLocation=None,
        )
        route = AsyncMock(return_value={"minutes": 10, "minutesMin": None, "minutesMax": None, "distanceMeters": 400, "source": "模拟路线", "polyline": []})
        with (
            patch("app.itinerary_doc.settings.amap_web_service_key", "test"),
            patch("app.itinerary_doc.verify_required_place", new=AsyncMock(side_effect=lambda p: p)),
            patch("app.itinerary_doc.search_places", new=AsyncMock(return_value=([], "高德"))),
            patch("app.itinerary_doc._model", new=AsyncMock(return_value='{"days":[[{"placeId":"d","experience":"公园漫步","timeWindow":"morning"},{"placeId":"e","experience":"参观博物馆","timeWindow":"evening"}]]}')),
            patch("app.trip_intent.calculate_route", new=route),
            patch("app.itinerary_doc.collect_opening_hours", new=AsyncMock(return_value={"e": "18:00-22:00", "d": "09:00-17:00"})),
        ):
            result = await build_itinerary(request)
        stops = result["dayPlans"][0]["stops"]
        self.assertEqual([item["place"]["name"] for item in stops], ["日间公园", "夜间博物馆"])
        self.assertGreaterEqual(int(stops[1]["arrival"].split(":")[0]), 18)
        self.assertIsNone(stops[1]["warning"])


if __name__ == "__main__":
    unittest.main()
