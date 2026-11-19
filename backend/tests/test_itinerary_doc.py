import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.itinerary_doc import build_itinerary
from app.models import Place, Point
from app.places import calculate_route


def place(name, id, lng, lat, category="风景名胜"):
    return Place(placeId=id, name=name, category=category, address="武汉", location=Point(lng=lng, lat=lat), source="高德地图")


class ItineraryDocumentTest(unittest.IsolatedAsyncioTestCase):
    async def test_four_days_keep_required_places_and_route_modes(self):
        required = [place("黄鹤楼", "y", 114.302, 30.545), place("武昌路自行车租赁点", "b", 114.308, 30.543, "生活服务")]
        candidates = [
            place("东湖听涛景区", "e1", 114.377, 30.565), place("东湖梨园", "e2", 114.38, 30.57),
            place("汉口水塔", "h1", 114.29, 30.579), place("江汉关博物馆", "h2", 114.297, 30.576),
            place("晴川阁", "a1", 114.285, 30.556), place("古琴台", "a2", 114.264, 30.554),
        ]
        request = SimpleNamespace(mustVisitPlaces=required, people=2, days=4, requirements="慢节奏，多打车", startDate="2026-11-22", transport="打车 / 驾车", lodging="")
        route = AsyncMock(return_value={"minutes": 18, "minutesMin": None, "minutesMax": None, "distanceMeters": 3000, "source": "高德地图路线规划", "polyline": []})
        progress = AsyncMock()
        with patch("app.itinerary_doc.collect_opening_hours", new=AsyncMock(return_value={})), patch("app.itinerary_doc.settings.amap_web_service_key", "test"), patch("app.itinerary_doc.search_places", new=AsyncMock(return_value=(candidates, "高德地图 POI"))), patch("app.itinerary_doc._model", new=AsyncMock(return_value="[]")), patch("app.itinerary_doc.calculate_route", new=route):
            result = await build_itinerary(request, progress=progress)
        self.assertEqual(progress.await_args_list[0].args[0], 5)
        self.assertEqual(progress.await_args_list[-1].args[0], 97)
        self.assertGreaterEqual(progress.await_count, 6)
        markdown = result["markdown"]
        self.assertEqual(re.findall(r"^## Day (\d+)", markdown, re.M), ["1", "2", "3", "4"])
        self.assertEqual(markdown.count("【必去】"), 2)
        self.assertIn("武昌路自行车租赁点", markdown.split("## Day 2")[0])
        self.assertIn("停留办理或查看约 20 分钟", markdown)
        self.assertIn("预计驾车约 18 分钟", markdown)
        self.assertEqual(len(result["recommendedPlaces"]), 6)
        self.assertEqual(len(result["dayPlans"]), 4)
        self.assertEqual({stop["place"]["name"] for day in result["dayPlans"] for stop in day["stops"]}, {place.name for place in required + candidates})
        self.assertTrue(all(day["stops"] and len(day["legs"]) == len(day["stops"]) - 1 for day in result["dayPlans"]))
        self.assertTrue(markdown.rstrip().endswith("高峰期给餐饮、打车与过江交通预留额外时间。"))

    async def test_selected_lodging_point_skips_extra_geocoding(self):
        required = [place("黄鹤楼", "y", 114.302, 30.545)]
        lodging_point = Point(lng=114.31, lat=30.56)
        request = SimpleNamespace(
            mustVisitPlaces=required, people=1, days=1, requirements="",
            startDate=None, transport="打车 / 驾车",
            lodging="已选酒店", lodgingLocation=lodging_point,
        )
        route = AsyncMock(return_value={"minutes": 10, "minutesMin": None, "minutesMax": None, "distanceMeters": 1300, "source": "高德地图路线规划", "polyline": []})
        with patch("app.itinerary_doc.collect_opening_hours", new=AsyncMock(return_value={})), patch("app.itinerary_doc.settings.amap_web_service_key", "test"), patch("app.itinerary_doc.search_places", new=AsyncMock(return_value=([], "高德 POI"))), patch("app.itinerary_doc._model", new=AsyncMock(return_value=None)), patch("app.itinerary_doc.calculate_route", new=route), patch("app.itinerary_doc._lodging_point", new=AsyncMock(side_effect=AssertionError("应复用地图坐标"))):
            result = await build_itinerary(request)
        self.assertEqual(len(result["dayPlans"]), 1)
        self.assertEqual(result["dayPlans"][0]["stops"][0]["place"]["name"], "黄鹤楼")
        self.assertEqual(route.await_args_list[0].args[0], lodging_point)

    async def test_route_failure_has_labeled_range(self):
        with patch("app.places.settings.amap_web_service_key", ""):
            result = await calculate_route(Point(lng=114.3, lat=30.54), Point(lng=114.37, lat=30.56), "driving")
        self.assertLess(result["minutesMin"], result["minutesMax"])
        self.assertIn("非实时路线", result["source"])


if __name__ == "__main__":
    unittest.main()
